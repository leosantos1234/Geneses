import os
print("BACKTESTING_GERAL - LOADED FROM:", __file__)
import sys

import logging
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ["MODO_RELAXADO"] = "true"

# Agora seus outros imports, funÃ§Ãµes e lÃ³gica.


# ImportaÃ§Ãµes dos mÃ³dulos internos
from estrategia_entrada.estrategia_breakout_filtros import executar_backtest_breakout, confirmar_entrada_com_criterios
from estrategia_entrada.estrategia_market_making_lateral import executar_market_making
from estrategias.analise_sentimento import analisar_sentimento
from utils.indicadores import calculate_indicators, detectar_tendencia, calcular_volatilidade
from exchange.factory import create_exchange_client
# O backtest nÃ£o deve abrir uma sessÃ£o de exchange ao ser importado.
# A sessÃ£o sÃ³ Ã© criada no bloco __main__, quando necessÃ¡ria.
session = None
BYBIT_API_KEY = None  # compatibilidade com o adaptador histÃ³rico nÃ£o utilizado pelo funil
BYBIT_API_SECRET = None
import requests
from regime.regime_detector import RegimeDetector
from regime.entry_profile import STRATEGY_REGIMES
from estrategia_entrada.market_making_rules import canonical_config, quote_from_closes


def _pullback_regime_allowed(regime: int | None) -> bool:
    """Keep live eligibility aligned with the canonical Pullback backtest when enabled."""
    if os.getenv("PULLBACK_BACKTEST_PARITY", "0").strip().lower() in ("1", "true", "yes", "on"):
        return regime is not None and int(regime) != 4
    return regime in {1, 2}
from utils.trade_authorization_gate import autorizar_trade
from trade_quality.trade_classifier import (
    calcular_metricas_backtest_trade_quality,
    extrair_features_trade,
    prever_probabilidade_trade,
)
from estrategia_entrada.swing_trend_following.strategy import (
    backtest as backtest_swing_trend_following,
    backtest_entrada_1h,
    avaliar_setup as avaliar_setup_swing_trend_following,
    avaliar_setup_entrada_1h,
    classificar_regime_swing,
)
from estrategia_entrada.pullback_trend_following.strategy import (
    backtest_entrada_1h as backtest_pullback_trend_following,
    avaliar_setup_entrada_1h as avaliar_setup_pullback_trend_following,
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _aplicar_custos_realistas_backtest(resultado: dict) -> dict:
    """Desconta custos de execução do resultado bruto de qualquer estratégia.

    As estratégias calculam o PnL bruto. Este pós-processamento padroniza taxa,
    slippage e funding sem interferir no executor real da OKX.
    """
    if not isinstance(resultado, dict) or resultado.get("custos_simulados_aplicados"):
        return resultado

    trades = resultado.get("trade_log")
    if not isinstance(trades, list) or not trades:
        resultado["custos_simulados"] = 0.0
        resultado["funding_simulado"] = 0.0
        resultado["custos_simulados_aplicados"] = True
        resultado["alavancagem_simulada"] = _env_float("LEVERAGE", 2.0)
        return resultado

    # Taxas por lado. A API da OKX pode sobrescrever estes valores quando
    # sincronizada; os defaults são apenas fallback para backtest offline.
    legacy_fee = _env_float("BACKTEST_FEE_PCT_PER_SIDE", 0.0005)
    maker_fee = max(0.0, _env_float("BACKTEST_MAKER_FEE_PCT_PER_SIDE", legacy_fee))
    taker_fee = max(0.0, _env_float("BACKTEST_TAKER_FEE_PCT_PER_SIDE", legacy_fee))
    funding_8h = max(0.0, _env_float("BACKTEST_FUNDING_PCT_8H", 0.0001))
    leverage = max(1.0, _env_float("LEVERAGE", 2.0))
    symbol = str(resultado.get("symbol") or os.getenv("ATIVOS", "")).upper().strip()
    asset = symbol.replace("-USDT-SWAP", "").replace("USDT", "").split(",")[0]
    default_slippage = {
        "BTC": 0.00010,
        "ETH": 0.00015,
        "BNB": 0.00020,
        "SOL": 0.00025,
        "XRP": 0.00030,
    }.get(asset, 0.00020)
    slippage = max(0.0, _env_float(f"BACKTEST_SLIPPAGE_PCT_PER_SIDE_{asset}", default_slippage))

    funding_history = None
    funding_path = os.getenv(f"BACKTEST_FUNDING_HISTORY_{asset}_CSV", os.getenv("BACKTEST_FUNDING_HISTORY_CSV", ""))
    if funding_path and not os.path.isabs(funding_path) and not os.path.exists(funding_path):
        funding_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", funding_path))
    if funding_path and os.path.exists(funding_path):
        try:
            funding_history = pd.read_csv(funding_path)
            time_col = "time" if "time" in funding_history.columns else "timestamp"
            rate_col = "funding_rate" if "funding_rate" in funding_history.columns else "rate"
            if time_col not in funding_history.columns or rate_col not in funding_history.columns:
                funding_history = None
            else:
                raw_time = funding_history[time_col]
                numeric_time = pd.to_numeric(raw_time, errors="coerce")
                if numeric_time.notna().sum() >= max(1, int(len(raw_time) * 0.8)):
                    funding_history[time_col] = pd.to_datetime(numeric_time, unit="ms", errors="coerce")
                else:
                    funding_history[time_col] = pd.to_datetime(raw_time, errors="coerce")
                funding_history[rate_col] = pd.to_numeric(funding_history[rate_col], errors="coerce")
                funding_history = funding_history.dropna(subset=[time_col, rate_col]).sort_values(time_col)
        except Exception as exc:
            logging.warning("[CUSTOS] Falha ao carregar funding histórico %s: %s", funding_path, exc)
            funding_history = None
    total_cost = 0.0
    total_funding = 0.0

    for trade in trades:
        if not isinstance(trade, dict):
            continue
        try:
            entry = float(trade.get("entry_price", trade.get("preco_entrada", trade.get("entry", 0))) or 0)
            exit_price = float(trade.get("exit_price", trade.get("preco_saida", trade.get("exit", 0))) or 0)
            qty = abs(float(trade.get("qty", 0) or 0))
        except (TypeError, ValueError):
            continue
        if entry <= 0 or exit_price <= 0 or qty <= 0:
            trade["custos_simulados"] = 0.0
            trade["funding_simulado"] = 0.0
            trade["custos_incompletos"] = True
            continue

        notional_entry = entry * qty
        notional_exit = exit_price * qty
        liquidity = str(trade.get("liquidity", "taker")).strip().lower()
        fee = maker_fee if liquidity == "maker" else taker_fee
        execution_cost = (notional_entry + notional_exit) * (fee + slippage)

        start = trade.get("entry_time", trade.get("data_entrada"))
        end = trade.get("exit_time", trade.get("data"))
        funding_cost = 0.0
        try:
            if start is not None and end is not None:
                start_dt = pd.to_datetime(start)
                end_dt = pd.to_datetime(end)
                if funding_history is not None:
                    time_col = "time" if "time" in funding_history.columns else "timestamp"
                    rate_col = "funding_rate" if "funding_rate" in funding_history.columns else "rate"
                    events = funding_history[(funding_history[time_col] > start_dt) & (funding_history[time_col] <= end_dt)]
                    funding_cost = notional_entry * float(events[rate_col].sum())
                    trade["funding_eventos"] = int(len(events))
                else:
                    hours = max(0.0, (end_dt - start_dt).total_seconds() / 3600.0)
                    funding_cost = notional_entry * funding_8h * (hours / 8.0)
        except Exception:
            trade["funding_indisponivel"] = True

        cost = execution_cost + funding_cost
        gross = float(trade.get("lucro", trade.get("pnl", trade.get("pnl_abs", 0.0))) or 0.0)
        net = gross - cost
        trade["pnl_bruto"] = gross
        trade["custos_execucao"] = round(execution_cost, 10)
        trade["liquidity"] = "maker" if liquidity == "maker" else "taker"
        trade["taxa_aplicada"] = fee
        trade["funding_simulado"] = round(funding_cost, 10)
        trade["custos_simulados"] = round(cost, 10)
        trade["margem_simulada"] = round(notional_entry / leverage, 10)
        trade["lucro_liquido"] = net
        if "lucro" in trade:
            trade["lucro"] = net
        if "pnl" in trade:
            trade["pnl"] = net
        if "pnl_abs" in trade:
            trade["pnl_abs"] = net
        total_cost += cost
        total_funding += funding_cost

    initial = float(resultado.get("saldo_inicial", 0.0) or 0.0)
    if initial <= 0:
        initial = _env_float("BACKTEST_CAPITAL", 0.0)
    gross_total = float(resultado.get("lucro_total", resultado.get("lucro", 0.0)) or 0.0)
    net_total = gross_total - total_cost
    resultado["lucro_bruto"] = gross_total
    resultado["lucro_total"] = net_total
    resultado["saldo_final"] = initial + net_total if initial > 0 else resultado.get("saldo_final", net_total)
    resultado["retorno_pct"] = (net_total / initial * 100.0) if initial > 0 else 0.0
    resultado["custos_simulados"] = total_cost
    resultado["funding_simulado"] = total_funding
    resultado["taxa_maker_por_lado"] = maker_fee
    resultado["taxa_taker_por_lado"] = taker_fee
    resultado["slippage_por_lado"] = slippage
    resultado["funding_por_8h"] = funding_8h
    resultado["alavancagem_simulada"] = leverage
    resultado["custos_simulados_aplicados"] = True
    return resultado


def _registrar_operacoes_por_regime(resultado: dict) -> dict:
    """Agrega quantidade e PnL por regime sem descartar trades sem etiqueta."""
    if not isinstance(resultado, dict):
        return resultado
    resumo = {str(regime): {"trades": 0, "pnl": 0.0, "wins": 0} for regime in (1, 2, 3, 4)}
    desconhecido = 0
    for trade in resultado.get("trade_log", []) or []:
        try:
            regime = int(trade.get("regime"))
        except (AttributeError, TypeError, ValueError):
            regime = 0
        if str(regime) not in resumo:
            desconhecido += 1
            continue
        pnl = float(trade.get("lucro", trade.get("pnl", trade.get("pnl_abs", 0.0))) or 0.0)
        item = resumo[str(regime)]
        item["trades"] += 1
        item["pnl"] += pnl
        item["wins"] += int(pnl > 0)
    resultado["trades_por_regime"] = resumo
    resultado["trades_regime_desconhecido"] = desconhecido
    return resultado

def _log_temporal_order(df: pd.DataFrame, origem: str) -> None:
    """Log temporario para auditar ordem temporal dos candles."""
    try:
        if df is None or df.empty or "time" not in df.columns:
            logging.info(f"[TEMP-CANDLE-ORDER] origem={origem} sem_dados")
            return
        head_ts = [str(x) for x in df["time"].head(3).tolist()]
        tail_ts = [str(x) for x in df["time"].tail(3).tolist()]
        inc = bool(df["time"].is_monotonic_increasing)
        dec = bool(df["time"].is_monotonic_decreasing)
        logging.info(
            "[TEMP-CANDLE-ORDER] origem=%s head3=%s tail3=%s monotonic_inc=%s monotonic_dec=%s",
            origem,
            head_ts,
            tail_ts,
            inc,
            dec,
        )
    except Exception as e:
        logging.warning(f"[TEMP-CANDLE-ORDER] falha_origem={origem}: {e}")


def _anotar_trade_quality_backtest(df: pd.DataFrame, resultado: dict, regime: str) -> dict:
    if not isinstance(resultado, dict):
        return resultado

    trade_log = resultado.get("trade_log", [])
    if not isinstance(trade_log, list) or not trade_log:
        resultado["trade_quality_backtest"] = calcular_metricas_backtest_trade_quality([])
        return resultado

    anotados = []
    for trade in trade_log:
        trade_item = dict(trade or {})
        try:
            entry_pos = int(trade_item.get("entry_pos"))
            if df is not None and not df.empty and 0 <= entry_pos < len(df):
                df_slice = df.iloc[: entry_pos + 1].copy()
                features = extrair_features_trade(
                    df_slice,
                    {
                        "estrategia": resultado.get("estrategia"),
                        "tendencia": regime,
                        "regime": regime,
                        "score_entrada": trade_item.get(
                            "score",
                            resultado.get("score", resultado.get("score_calculado", 0.0)),
                        ),
                        "sentimento": "neutro",
                        "lstm_signal": 0,
                    },
                )
                prob, status_modelo = prever_probabilidade_trade(features)
                trade_item["trade_quality_prob"] = round(float(prob), 4)
                trade_item["trade_quality_modelo_status"] = status_modelo
        except Exception:
            pass
        anotados.append(trade_item)

    resultado["trade_log"] = anotados
    resultado["trade_quality_backtest"] = calcular_metricas_backtest_trade_quality(anotados)
    return resultado



# === FLAGS DE CONTROLE DAS ESTRATÃ‰GIAS (True = desativada no backtest) ===
DESATIVAR_REVERSAO = False
DESATIVAR_MARKETMAKING = False # True

# === DICIONÃRIO DE ESTRATÃ‰GIAS COM CONTROLE DE ATIVAÃ‡ÃƒO ===
ESTRATEGIAS = {}

# EstratÃ©gia de reversÃ£o
if not DESATIVAR_REVERSAO:
    ESTRATEGIAS["MEAN_REVERSION"] = lambda df, direcao, capital_inicial: criar_backtest_reversao(
        session, BYBIT_API_KEY, BYBIT_API_SECRET)(df, direcao, capital_inicial)
else:
    ESTRATEGIAS["MEAN_REVERSION"] = None # lambda df, direcao, capital_inicial: {
        #"lucro": -9999,
        #"mensagem": "âŒ EstratÃ©gia REVERSÃƒO desativada via flag"
    #}

# EstratÃ©gia de market making lateral (catÃ¡logo apenas; regime filtra no funil)
if not DESATIVAR_MARKETMAKING:
    ESTRATEGIAS["MARKET_MAKING_LATERAL"] = lambda df, direcao, capital_inicial: _backtest_market_making_lateral(
        df.copy(), capital_inicial
    )
else:
    ESTRATEGIAS["MARKET_MAKING_LATERAL"] = None

# === LIMPA ESTRATÃ‰GIAS DESATIVADAS ===
ESTRATEGIAS = {k: v for k, v in ESTRATEGIAS.items() if v is not None}

# EstratÃ©gia de breakout (sempre ativa)
ESTRATEGIAS["BREAKOUT"] = lambda df, direcao, capital_inicial: executar_backtest_breakout(
    df.copy(),
    lado_permitido=direcao,
    capital_inicial=capital_inicial,
)

_catalog_strategy = os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
if _catalog_strategy and _catalog_strategy != "SWING_AND_PULLBACK":
    logging.info(
        "[STRATEGY_CATALOG] worker fixo carregado: %s",
        _catalog_strategy,
    )
else:
    logging.info("[STRATEGY_CATALOG] estratégias disponíveis: %s", list(ESTRATEGIAS.keys()))


#ESTRATEGIAS = {
 #   "breakout": lambda df, direcao, capital_inicial: executar_backtest_breakout(df),
  #  "MEAN_REVERSION": lambda df, direcao, capital_inicial: criar_backtest_reversao(session, BYBIT_API_KEY, BYBIT_API_SECRET)(df, direcao, capital_inicial),
   # "marketmaking": lambda df, direcao, capital_inicial: criar_backtest_marketmaking(df, saldo_inicial=capital_inicial)

#}




# Pesos atribuÃ­dos Ã s fontes de decisÃ£o para score final
PESOS = {
    "lstm": 30,
    "openai": 30,
    "indicadores": 20,
    "volatilidade": 10,
    "tendencia": 10
}


def _backtest_market_making_lateral(df, capital_inicial=10000):
    """Simulador determinÃ­stico de Market Making para o backtest.

    A cada candle as ordens anteriores sÃ£o canceladas e duas novas cotas
    (bid/ask) sÃ£o repostadas. O preenchimento Ã© parcial quando o candle apenas
    toca o preÃ§o, o inventÃ¡rio Ã© limitado e uma parada de inventÃ¡rio achata a
    posiÃ§Ã£o pelo preÃ§o do candle. NÃ£o Ã© usado pelo executor real.
    """
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < 55:
        return {
            "estrategia": "MARKET_MAKING_LATERAL",
            "sem_trade": True,
            "motivo": "df_curto",
            "saldo_inicial": capital_inicial,
            "saldo_final": capital_inicial,
            "lucro_total": 0.0,
            "trade_log": [],
        }

    def _mm_float(name, default):
        try:
            return float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return float(default)

    work = df.copy().reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    if len(work) < 55:
        return {"estrategia": "MARKET_MAKING_LATERAL", "sem_trade": True, "motivo": "dados_invalidos"}

    saldo = float(capital_inicial)
    inventario = 0.0
    preco_medio = 0.0
    trade_log = []
    fills = 0
    partial_fills = 0
    ordens_canceladas = 0
    inventory_stops = 0
    regime_atual_mm = 3
    mm_symbol = (os.getenv("SYMBOL", "") or os.getenv("ATIVOS", "") or os.getenv("PAR", "")).strip().upper()
    mm_cfg = canonical_config()
    max_inventory = mm_cfg["max_inventory_qty"]
    stop_inventory = mm_cfg["stop_inventory_qty"]
    order_qty = mm_cfg["order_qty"]
    compound_reinvestment = str(os.getenv("MM_BACKTEST_COMPOUND", "0")).strip().lower() in {"1", "true", "yes", "on", "sim"}

    def _time(i):
        return str(work["time"].iloc[i]) if "time" in work.columns else str(i)

    def _fill_ratio(price, low, high, side):
        span = max(float(high) - float(low), 1e-12)
        penetration = ((float(price) - float(low)) / span) if side == "buy" else ((float(high) - float(price)) / span)
        return max(0.25, min(1.0, penetration))

    def _record_close(side, qty, entry, exit_price, entry_time, exit_time, reason):
        if qty <= 0 or entry <= 0 or exit_price <= 0:
            return
        pnl = (exit_price - entry) * qty if side == "buy" else (entry - exit_price) * qty
        trade_log.append({
            "entry_price": entry,
            "exit_price": exit_price,
            "qty": qty,
            "side": side,
            "lucro": pnl,
            "liquidity": "maker",
            "regime": regime_atual_mm,
            "motivo_saida": reason,
            "data_entrada": entry_time,
            "data": exit_time,
        })

    def _flatten(price, timestamp, reason):
        nonlocal saldo, inventario, preco_medio, entry_time
        if abs(inventario) <= 1e-12:
            return
        qty = abs(inventario)
        side = "buy" if inventario > 0 else "sell"
        _record_close(side, qty, preco_medio, price, entry_time, timestamp, reason)
        saldo += (-price * qty) if inventario > 0 else (price * qty)
        inventario = 0.0
        preco_medio = 0.0
        entry_time = None

    entry_time = None
    for i in range(50, len(work)):
        equity_scale = max(saldo / float(capital_inicial), 0.01) if compound_reinvestment and capital_inicial else 1.0
        active_order_qty = order_qty * equity_scale
        active_max_inventory = max_inventory * equity_scale
        active_stop_inventory = stop_inventory * equity_scale
        mid = float(work["close"].iloc[i])
        high = float(work["high"].iloc[i])
        low = float(work["low"].iloc[i])
        try:
            regime_atual_mm = int(RegimeDetector().detectar(work.iloc[: i + 1], symbol=mm_symbol or "DESCONHECIDO"))
        except Exception:
            regime_atual_mm = 3
        if regime_atual_mm != 3:
            if abs(inventario) > 1e-12:
                _flatten(mid, _time(i), "regime_change")
            ordens_canceladas += 2
            continue
        quotes = quote_from_closes(
            work["close"].iloc[max(0, i - 20):i],
            mid,
            inventario,
            active_max_inventory,
            mm_cfg,
        )
        bid = quotes["bid"]
        ask = quotes["ask"]
        ordens_canceladas += 2  # cancelamento/repost das ordens do candle anterior

        if low <= bid and inventario < active_max_inventory:
            qty = min(active_order_qty * _fill_ratio(bid, low, high, "buy"), active_max_inventory - inventario)
            if qty > 0:
                saldo -= bid * qty
                if inventario >= 0:
                    preco_medio = ((preco_medio * inventario) + (bid * qty)) / (inventario + qty)
                    inventario += qty
                else:
                    close_qty = min(qty, abs(inventario))
                    _record_close("sell", close_qty, preco_medio, bid, entry_time, _time(i), "buy_to_close")
                    inventario += close_qty
                    remaining = qty - close_qty
                    if remaining > 0:
                        inventario = remaining
                        preco_medio = bid
                        entry_time = _time(i)
                if abs(inventario) > 0 and entry_time is None:
                    entry_time = _time(i)
                fills += 1
                partial_fills += int(qty < active_order_qty)

        if high >= ask and inventario > -active_max_inventory:
            qty = min(active_order_qty * _fill_ratio(ask, low, high, "sell"), active_max_inventory + inventario)
            if qty > 0:
                saldo += ask * qty
                if inventario > 0:
                    close_qty = min(qty, inventario)
                    _record_close("buy", close_qty, preco_medio, ask, entry_time, _time(i), "sell_to_close")
                    inventario -= close_qty
                    if qty > close_qty:
                        inventario -= qty - close_qty
                        preco_medio = ask
                        entry_time = _time(i)
                else:
                    preco_medio = ((preco_medio * abs(inventario)) + (ask * qty)) / (abs(inventario) + qty) if inventario < 0 else ask
                    inventario -= qty
                if abs(inventario) > 0 and entry_time is None:
                    entry_time = _time(i)
                elif abs(inventario) <= 1e-12:
                    entry_time = None
                fills += 1
                partial_fills += int(qty < active_order_qty)

        if abs(inventario) >= active_stop_inventory:
            inventory_stops += 1
            _flatten(mid, _time(i), "inventory_stop")

    if abs(inventario) > 1e-12:
        _flatten(float(work["close"].iloc[-1]), _time(len(work) - 1), "end_of_data")

    lucro = saldo - float(capital_inicial)
    wins = sum(1 for t in trade_log if float(t.get("lucro", 0.0)) > 0)
    return {
        "estrategia": "MARKET_MAKING_LATERAL",
        "saldo_inicial": float(capital_inicial),
        "saldo_final": float(saldo),
        "lucro_total": float(lucro),
        "retorno_pct": (lucro / capital_inicial * 100.0) if capital_inicial else 0.0,
        "num_trades": len(trade_log),
        "winrate": (wins / len(trade_log) * 100.0) if trade_log else 0.0,
        "trade_log": trade_log,
        "fills": fills,
        "partial_fills": partial_fills,
        "ordens_canceladas": ordens_canceladas,
        "inventory_stops": inventory_stops,
        "max_inventory_qty": max_inventory,
        "compound_reinvestment": compound_reinvestment,
        "relatorio_completo": f"MM: trades={len(trade_log)} fills={fills} partial={partial_fills} inventory_stops={inventory_stops}",
    }


def _backtest_early_trend(df, direcao, capital_inicial=10000, regime_override=None, symbol=None):
    """Adapter para Early Trend (Swing/Recupera??o) no funil RN/RT 1.0."""
    try:
        from estrategias.early_trend.early_trend_backtest import backtest_early_trend

        return backtest_early_trend(
            df_original=df,
            direcao=direcao,
            capital_inicial=capital_inicial,
            regime_override=regime_override,
            symbol=symbol,
        )
    except Exception as e:
        logging.error(f"[EARLYTREND] Erro no backtest: {e}")
        return {"sem_trade": True, "erro": str(e)}





def _backtest_mean_reversion_stop_limit(df, direcao, capital_inicial=1000):
    """Adapter para a Mean Reversion nova (FSM + stop-limit como prova de mercado).

    MantÃ©m o formato esperado pelo funil:
    - retorna dict com `lucro_total` (abs), `saldo_inicial`, `saldo_final`, `winrate`, `relatorio_completo`
    - nÃ£o exige nÃºmero mÃ­nimo de trades: 0 trades -> lucro_total=0 (vÃ¡lido)
    """
    try:
        from estrategias.mean_reversion.mr_backtest import backtest_mean_reversion

        resultado = backtest_mean_reversion(df=df, symbol=os.getenv("SYMBOL", "UNKNOWN"))
        saldo_inicial = float(capital_inicial)
        saldo = float(saldo_inicial)
        risco_percentual = float(os.getenv("RISCO_PERCENTUAL", 0.004))

        trades = list(resultado.trades or [])
        trades_ordenados = sorted(trades, key=lambda t: int(getattr(t, "entry_pos", 0)))
        wins = 0
        trade_log = []

        for t in trades_ordenados:
            entry = float(t.entry_price)
            exit_price = float(t.exit_price)
            stop = float(t.stop_loss)
            stop_distance = abs(entry - stop)
            qty = (saldo * risco_percentual / stop_distance) if stop_distance > 0 else 0.0
            pnl = (exit_price - entry) * qty
            saldo += pnl
            if pnl > 0:
                wins += 1

            trade_log.append(
                {
                    "entry_pos": t.entry_pos,
                    "exit_pos": t.exit_pos,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "stop_loss": t.stop_loss,
                    "take_profit": t.take_profit,
                    "pnl_pct": t.pnl_pct,
                    "qty": qty,
                    "pnl_abs": pnl,
                    "regime": getattr(t, "regime", None),
                }
            )

        lucro_total = float(saldo - saldo_inicial)
        saldo_final = float(saldo)
        winrate = float((wins / len(trades_ordenados)) * 100.0) if trades_ordenados else 0.0

        out = {
            "estrategia": "MEAN_REVERSION",
            "direcao": "buy",
            "lucro_total": lucro_total,
            "saldo_inicial": saldo_inicial,
            "saldo_final": saldo_final,
            "winrate": winrate,
            "retorno_pct": float((lucro_total / saldo_inicial) * 100.0) if saldo_inicial else 0.0,
            "relatorio_completo": f"MR(FSM/StopLimit): trades={len(trades_ordenados)} wins={wins} lucro={lucro_total:.2f}",
        }

        # SÃ³ inclui trade_log se houver trades, para nÃ£o ser descartado como sem_trade pelo funil.
        if trade_log:
            out["trade_log"] = trade_log

        return out

    except Exception as e:
        logging.error(f"[MR][FSM] Erro no backtest stop-limit: {e}")
        return {"sem_trade": True, "erro": str(e)}

def carregar_dados_historicos(session, symbol, interval="60", limite=100):
    try:
        response = session.get_kline(
            category="linear",  # Corrigido para Futuros PERPÃ‰TUOS
            symbol=symbol,
            interval=interval,
            limit=limite
        )

        if response.get("retCode") != 0:
            logging.error(f"Erro na API Bybit: {response.get('retMsg')} (retCode {response.get('retCode')})")
            return None

        df = pd.DataFrame(response["result"]["list"], columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        df[["open", "high", "low", "close", "volume", "turnover"]] = df[["open", "high", "low", "close", "volume", "turnover"]].astype(float)
        df["time"] = pd.to_datetime(pd.to_numeric(df["time"], errors="coerce"), unit="ms")
        df = df.sort_values("time").reset_index(drop=True)
        if not df["time"].is_monotonic_increasing:
            logging.warning("Candles fora de ordem detectados. Corrigindo automaticamente.")
            df = df.sort_values("time").reset_index(drop=True)
        _log_temporal_order(df, "estrategia_entrada.backtesting_geral.carregar_dados_historicos")

        #logging.info(f"{len(df)} candles carregados com sucesso do mercado PERPÃ‰TUO!")
        return df

    except Exception as e:
        logging.error(f"Erro ao carregar dados histÃ³ricos PERPÃ‰TUO: {e}")
        return None


def obter_previsao_next_candle(df_lstm):
    url = os.getenv("LSTM_API_URL")

    payload = {"data": df_lstm.values.tolist()}

    try:
        r = requests.post(url, json=payload, timeout=3)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        logging.error(f"[LSTM SERVICE] Erro: {e}")
        return None




def imprimir_ranking_estrategias(resultados):
    """
    resultados = {
        "marketmaking": {"lucro_pct": 9.15, "lucro_abs": 91.49},
        "breakout": {"lucro_pct": -8.90, "lucro_abs": -8908.50},
        "MEAN_REVERSION": None
    }
    """

    print("\n\nðŸ† RESULTADO DA DISPUTA ENTRE ESTRATÃ‰GIAS")
    print("----------------------------------------")

    # Ordenar estratÃ©gias ativas por lucro percentual
    ativos = {
        k: v for k, v in resultados.items()
        if v is not None and v.get("lucro_pct") is not None
    }

    if not ativos:
        print("âš  Nenhuma estratÃ©gia ativa disponÃ­vel para ranking.")
        return None

    ranking = sorted(ativos.items(), key=lambda x: x[1]["lucro_pct"], reverse=True)

    posicao = 1
    for nome, dados in ranking:
        print(f"{posicao}Âº lugar: {nome.upper()}")
        print(f"- Lucro (%): {dados['lucro_pct']:+.2f}%")
        print(f"- Lucro (R$): {dados['lucro_abs']:+.2f}")
        print("")
        posicao += 1

    # EstratÃ©gias desativadas / sem resultado
    desativadas = [k for k, v in resultados.items() if v is None]
    for nome in desativadas:
        print(f"{posicao}Âº lugar: {nome.upper()} (desativada)")
        posicao += 1

    print("----------------------------------------")

    # Escolher vencedora
    if ranking:
        vencedora = ranking[0][0]
        print(f"ðŸŽ¯ EstratÃ©gia escolhida para operar: {vencedora.upper()}\n")
        return vencedora
    else:
        print("âš  Nenhuma estratÃ©gia ativa disponÃ­vel.")
        return None






def calcular_vencedora_do_ranking(resultados_ranking):
    """Retorna apenas o nome da estratÃ©gia vencedora."""
    melhor = None
    melhor_pct = -999999

    for nome, dados in resultados_ranking.items():
        if dados is None:
            continue

        pct = dados.get("lucro_pct", None)
        if pct is not None and pct > melhor_pct:
            melhor_pct = pct
            melhor = nome

    return melhor




def salvar_direcao_global(direcao):
    """Compatibilidade legada: direcao agora e sempre explicita no funil."""
    logging.info(
        f"[DECISION ENGINE] salvar_direcao_global chamada em modo legado (ignorada). direcao={str(direcao).upper()}"
    )





def filtro_lstm_backtest(
    lstm_result,
    side,
    atr,
    conf_min=0.002,
    atr_mult_min=0.3
):
    if not lstm_result:
        return False

    delta = lstm_result["delta"]
    confidence = lstm_result["confidence"]
    direction = lstm_result["direction"]

    if confidence < conf_min:
        return False

    if abs(delta) < atr_mult_min * atr:
        return False

    if side == "buy" and direction != "up":
        return False

    if side == "sell" and direction != "down":
        return False

    return True



def executar_backtest_completo(df, sentimento, capital_inicial=1000, regime_override=None, approved_side=None,
                               natural_runtime_context=None, breakout_decision_trace=None,
                               breakout_trace_candle_open_time=None, swing_timeframes=None,
                               reference_data=None):
    _ = df, sentimento, capital_inicial
    logging.info("[FUNIL] Backtest completo (multi-estrat?gia) iniciado.")

    try:
        janela = int(os.getenv("BACKTEST_WINDOW", "400"))
    except Exception:
        janela = 400

    # A janela operacional continua limitada para as estratégias intraday.
    # Market making, porém, pode receber explicitamente o mesmo histórico
    # completo usado no backtest de referência (por exemplo, BTC 5m/1 ano).
    mm_reference_df = reference_data.copy() if isinstance(reference_data, pd.DataFrame) else None

    if df is not None and len(df) > janela > 0:
        df = df.tail(janela).copy()

    if df is None or len(df) < 50:
        logging.warning(
            f"[FUNIL] DF muito curto ({len(df) if df is not None else 0} candles) minimo recomendado: 50 candles. Cancelando backtest."
        )
        return None, [], {}

    approved_side_raw = (approved_side or "").strip().lower()
    direcao_contexto = approved_side_raw if approved_side_raw in ["buy", "sell"] else None

    contexto_decisao = {
        "direcao": direcao_contexto,
        "confianca": 1.0,
        "placar": {"buy": 0, "sell": 0},
        "autorizado": True,
    }

    lado_permitido = contexto_decisao["direcao"]
    logging.info(
        f"[BACKTEST-GERAL] Avaliando contexto para lado aprovado: "
        f"{lado_permitido.upper() if lado_permitido else 'AUTO (BUY/SELL)'}"
    )
    logging.info(
        f"[DECISION ENGINE] Approved Side = {lado_permitido.upper() if lado_permitido else 'AUTO'}"
    )

    symbol_env = (
        os.getenv("SYMBOL", "")
        or os.getenv("ATIVOS", "")
        or os.getenv("PAR", "")
    ).strip().upper()
    if not symbol_env:
        try:
            from utils.parse_env import get_ativos_env

            ativos = get_ativos_env() or []
            symbol_env = (ativos[0] if ativos else "").strip().upper()
        except Exception:
            symbol_env = ""
    logging.info(f"[SIM][REGIME] Symbol detectado para regime: {symbol_env or 'DESCONHECIDO'}")

    def _swing_exit_kwargs():
        """Retorna a calibração de saída por ativo sem alterar o padrão global."""
        asset = symbol_env.upper()
        prefix = "SWING_XRP_" if "XRP" in asset else "SWING_"
        def _float(name, default):
            try:
                return float(os.getenv(prefix + name, str(default)))
            except (TypeError, ValueError):
                return float(default)
        return {
            "stop_atr": _float("STOP_ATR", 2.0),
            "target_atr": _float("TARGET_ATR", 4.0),
            "trailing_atr": _float("TRAILING_ATR", 2.5),
        }

    try:
        if regime_override in (1, 2, 3, 4):
            regime = int(regime_override)
            logging.info(f"[SIM][REGIME] Regime override aplicado: {regime}")
        else:
            regime = RegimeDetector().detectar(df, symbol=symbol_env or "DESCONHECIDO")
    except Exception as e:
        logging.warning(f"[BACKTEST-GERAL] Falha ao detectar regime, usando fallback 2: {e}")
        regime = 2
    logging.info(f"[SIM][REGIME] Regime detectado para simula??o/diagn?stico: {regime}")

    # Histórico temporal de regimes. O regime atual continua protegendo a
    # execução, mas a seleção da estratégia considera toda a janela.
    regime_historico: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    regime_min_candles = 30
    for idx in range(regime_min_candles, len(df)):
        try:
            regime_i = int(RegimeDetector().detectar(df.iloc[: idx + 1], symbol=symbol_env or "DESCONHECIDO"))
            if regime_i in regime_historico:
                regime_historico[regime_i] += 1
        except Exception:
            continue
    if not any(regime_historico.values()):
        regime_historico[regime] = max(1, len(df) - regime_min_candles)
    regimes_observados = {r for r, n in regime_historico.items() if n > 0}
    logging.info("[SIM][REGIME-HISTORY] %s", regime_historico)

    # O funil usa o regime atual para escolher candidatas, mas cada estratégia
    # pode recalcular o regime candle a candle durante a simulação histórica.
    # Uma vela natural PRODUCTION_RUNTIME é exceção: sua autoridade canônica
    # deve atravessar o funil sem ser anulada pelo modo dinâmico do backtest.
    from utils.breakout_runtime_context import select_backtest_regime_override

    regime_override_backtest = select_backtest_regime_override(
        regime=regime,
        natural_runtime_context=natural_runtime_context,
        dynamic_regime_enabled=os.getenv("BACKTEST_DYNAMIC_REGIME", "true").strip().lower()
        in ("1", "true", "yes", "on"),
    )

    # ======================================================
    # ELEGIBILIDADE POR REGIME (RN/RT 1.0)
    # - estratÃ©gias sÃ³ competem no seu regime natural
    # - regime 4 Ã© NO-TRADE
    # ======================================================
    candidatas = {}
    # A seleção explícita do worker tem precedência sobre o sinalizador
    # legado BREAKOUT_ONLY. Sem essa precedência, um worker configurado para
    # Pullback ainda entrava no funil Breakout e nunca chegava ao setup 1h/4h/1d.
    selected_strategy = os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
    fixed_strategy_mode = bool(selected_strategy and selected_strategy != "SWING_AND_PULLBACK")
    strategy_requires_pullback_flow = selected_strategy in {
        "PULLBACK_TREND_FOLLOWING",
        "SWING_TREND_FOLLOWING",
        "SWING_AND_PULLBACK",
    }
    strategy_requires_market_making_flow = selected_strategy == "MARKET_MAKING_LATERAL"
    breakout_only = (
        os.getenv("BREAKOUT_ONLY", "0").strip().lower() in ("1", "true", "yes")
        and not strategy_requires_pullback_flow
        and not strategy_requires_market_making_flow
    )
    if (strategy_requires_pullback_flow or strategy_requires_market_making_flow) and os.getenv("BREAKOUT_ONLY", "0").strip().lower() in ("1", "true", "yes"):
        logging.info(
            "[STRATEGY_SELECTION] BREAKOUT_ONLY ignorado para seleção explícita=%s; "
            "a elegibilidade da estratégia selecionada será respeitada.",
            selected_strategy,
        )
    swing_enabled = os.getenv("SWING_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    pullback_enabled = os.getenv("PULLBACK_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    swing_history_valid = True
    if (swing_enabled or pullback_enabled) and swing_timeframes is not None and (
        os.getenv("SWING_HISTORICAL_BACKTEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
        or os.getenv("PULLBACK_HISTORICAL_BACKTEST_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    ):
        from estrategia_entrada.swing_trend_following.historical_cache import validate_swing_history

        swing_history_validation = validate_swing_history(
            swing_timeframes,
            months=int(os.getenv("SWING_HISTORICAL_MONTHS", "36")),
        )
        swing_history_valid = bool(swing_history_validation.get("valid"))
        logging.info("[FUNIL][SWING-HISTORY] valid=%s details=%s", swing_history_valid, swing_history_validation)
        if not swing_history_valid:
            logging.warning("[FUNIL][SWING-HISTORY] Swing removida do ranking: histórico insuficiente")

    if breakout_only:
        candidatas["BREAKOUT"] = lambda: executar_backtest_breakout(
            df.copy(),
            lado_permitido,
            regime_override=regime_override_backtest,
            symbol=symbol_env or None,
            capital_inicial=capital_inicial,
            natural_runtime_context=natural_runtime_context,
            decision_trace=breakout_decision_trace,
            trace_candle_open_time=breakout_trace_candle_open_time,
        )
        logging.info("[FUNIL] BREAKOUT_ONLY ativo: apenas BREAKOUT sera avaliada.")
        # BREAKOUT_ONLY continua preservando o breakout, mas o swing pode ser
        # habilitado explicitamente como candidata shadow/paper independente.
        if swing_enabled and swing_timeframes is not None and swing_history_valid:
            swing_1h = swing_timeframes.get("1h")
            swing_4h = swing_timeframes.get("4h")
            swing_daily = swing_timeframes.get("1d")
            swing_regime = classificar_regime_swing(swing_4h, swing_daily)
            if swing_regime in {1, 2}:
                if os.getenv("SWING_ENTRY_TIMEFRAME", "1h").strip().lower() == "1h":
                    candidatas["SWING_TREND_FOLLOWING"] = lambda: backtest_entrada_1h(
                        swing_1h.copy(), swing_4h.copy(), swing_daily.copy(), regime=swing_regime,
                        **_swing_exit_kwargs()
                    )
                else:
                    candidatas["SWING_TREND_FOLLOWING"] = lambda: backtest_swing_trend_following(
                        swing_4h.copy(), swing_daily.copy(), regime=swing_regime
                    )
                logging.info("[FUNIL] Swing habilitado em paralelo ao BREAKOUT | regime_swing=%s", swing_regime)
            else:
                logging.info("[FUNIL] Swing bloqueado pelo regime 4h/diário | regime_swing=%s", swing_regime)
        if pullback_enabled and swing_timeframes is not None and swing_history_valid:
            pullback_1h = swing_timeframes.get("1h")
            pullback_4h = swing_timeframes.get("4h")
            pullback_daily = swing_timeframes.get("1d")
            pullback_regime = classificar_regime_swing(pullback_4h, pullback_daily)
            if _pullback_regime_allowed(pullback_regime):
                candidatas["PULLBACK_TREND_FOLLOWING"] = lambda: backtest_pullback_trend_following(
                    pullback_1h.copy(), pullback_4h.copy(), pullback_daily.copy(), regime=pullback_regime
                )
                logging.info("[FUNIL] Pullback habilitado em paralelo | regime_swing=%s", pullback_regime)

    if (not breakout_only) and regime == 4:
        logging.info("[FUNIL][NO-TRADE] Regime 4 (caos): nenhuma estratÃ©gia elegÃ­vel.")
        return None, {"regime": regime, "candidatas": [], "sem_trade": []}, {}

    elif not breakout_only:
        # A janela inteira define as candidatas; o setup atual ainda funciona
        # como gate final antes da ordem real.
        # Keep backtest eligibility aligned with the canonical regime routing.
        # Copy the sets so this module cannot mutate the shared profile.
        strategy_regimes = {
            name: set(regimes) for name, regimes in STRATEGY_REGIMES.items()
        }
        if strategy_regimes["BREAKOUT"] & regimes_observados:
            candidatas["BREAKOUT"] = lambda: executar_backtest_breakout(
                df.copy(),
                lado_permitido,
                regime_override=regime_override_backtest,
                symbol=symbol_env or None,
                capital_inicial=capital_inicial,
                natural_runtime_context=natural_runtime_context,
                decision_trace=breakout_decision_trace,
                trace_candle_open_time=breakout_trace_candle_open_time,
            )
        if strategy_regimes["MEAN_REVERSION"] & regimes_observados:
            candidatas["MEAN_REVERSION"] = lambda: _backtest_mean_reversion_stop_limit(df.copy(), lado_permitido, capital_inicial)
        if strategy_regimes["EARLY_TREND"] & regimes_observados and os.getenv("EARLY_TREND_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
            candidatas["EARLY_TREND"] = lambda: _backtest_early_trend(
                df.copy(),
                lado_permitido,
                capital_inicial,
                regime_override=regime_override_backtest,
                symbol=symbol_env or None,
            )
        # Uma estratégia explicitamente configurada no worker precisa ser
        # avaliada historicamente mesmo quando o regime atual não é o seu
        # regime natural. O regime/setup atual continua sendo verificado pelo
        # probe e pela validação final antes de qualquer ordem.
        selected_market_making = selected_strategy == "MARKET_MAKING_LATERAL"
        if strategy_regimes["MARKET_MAKING_LATERAL"] & regimes_observados or selected_market_making:
            mm_df = mm_reference_df if mm_reference_df is not None else df
            candidatas["MARKET_MAKING_LATERAL"] = lambda: _backtest_market_making_lateral(mm_df.copy(), capital_inicial)
        if swing_enabled and swing_timeframes is not None and swing_history_valid:
            swing_1h = swing_timeframes.get("1h")
            swing_4h = swing_timeframes.get("4h")
            swing_daily = swing_timeframes.get("1d")
            swing_regime = classificar_regime_swing(swing_4h, swing_daily)
            if swing_regime in {1, 2}:
                if os.getenv("SWING_ENTRY_TIMEFRAME", "1h").strip().lower() == "1h":
                    candidatas["SWING_TREND_FOLLOWING"] = lambda: backtest_entrada_1h(
                        swing_1h.copy(), swing_4h.copy(), swing_daily.copy(), regime=swing_regime,
                        **_swing_exit_kwargs()
                    )
                else:
                    candidatas["SWING_TREND_FOLLOWING"] = lambda: backtest_swing_trend_following(
                        swing_4h.copy(), swing_daily.copy(), regime=swing_regime
                    )
                logging.info("[FUNIL] Swing elegível | regime_swing=%s", swing_regime)
            else:
                logging.info("[FUNIL] Swing fora do regime permitido | regime_swing=%s", swing_regime)
        if pullback_enabled and swing_timeframes is not None and swing_history_valid:
            pullback_1h = swing_timeframes.get("1h")
            pullback_4h = swing_timeframes.get("4h")
            pullback_daily = swing_timeframes.get("1d")
            pullback_regime = classificar_regime_swing(pullback_4h, pullback_daily)
            selected_pullback = selected_strategy == "PULLBACK_TREND_FOLLOWING"
            if _pullback_regime_allowed(pullback_regime) or selected_pullback:
                candidatas["PULLBACK_TREND_FOLLOWING"] = lambda: backtest_pullback_trend_following(
                    pullback_1h.copy(), pullback_4h.copy(), pullback_daily.copy(), regime=pullback_regime
                )
                logging.info(
                    "[FUNIL] Pullback histórico avaliado | regime_pullback=%s | "
                    "regime_atual_gate=%s",
                    pullback_regime,
                    "seleção explícita" if selected_pullback else "normal",
                )
            else:
                logging.info("[FUNIL] Pullback fora do regime permitido | regime_pullback=%s", pullback_regime)
        if not candidatas:
            logging.info(f"[BACKTEST-GERAL] Nenhuma estratégia teve regime elegível na janela.")
            return None, {"regime": regime, "regime_historico": regime_historico, "candidatas": [], "sem_trade": []}, {}

    # Em modo de estratégia fixa, o worker não deve executar os backtests das
    # demais estratégias antes de aplicar a seleção. Além de desperdiçar
    # processamento, isso polui o log com sinais de Breakout e pode produzir
    # métricas/telemetria de uma estratégia que não está autorizada a operar.
    # O modo combinado continua sendo a única exceção intencional.
    if fixed_strategy_mode:
        if selected_strategy not in candidatas:
            logging.warning(
                "[STRATEGY_SELECTION] estratégia=%s não elegível no regime=%s",
                selected_strategy,
                regime,
            )
            return None, {
                "regime": regime,
                "regime_historico": regime_historico,
                "candidatas": list(candidatas.keys()),
                "sem_trade": [selected_strategy],
                "strategy_selection": selected_strategy,
            }, {}
        candidatas = {selected_strategy: candidatas[selected_strategy]}
        logging.info(
            "[STRATEGY_SELECTION] ciclo fixo restrito antes do backtest: %s",
            selected_strategy,
        )

    combined_strategy = selected_strategy == "SWING_AND_PULLBACK"
    if combined_strategy:
        # Modo combinado: somente Swing e Pullback competem neste worker. O
        # ranking/gates escolhem no máximo uma vencedora por ciclo.
        candidatas = {
            name: func for name, func in candidatas.items()
            if name in {"SWING_TREND_FOLLOWING", "PULLBACK_TREND_FOLLOWING"}
        }
        if not candidatas:
            logging.warning("[STRATEGY_SELECTION] modo combinado sem candidatas Swing/Pullback elegíveis")
            return None, {"regime": regime, "candidatas": [], "sem_trade": ["SWING_AND_PULLBACK"], "strategy_selection": "SWING_AND_PULLBACK"}, {}
        logging.info("[STRATEGY_SELECTION] modo combinado restrito a SWING_TREND_FOLLOWING + PULLBACK_TREND_FOLLOWING")
    elif selected_strategy:
        if selected_strategy not in candidatas:
            logging.warning("[STRATEGY_SELECTION] estratégia=%s não elegível no regime=%s", selected_strategy, regime)
            return None, {"regime": regime, "regime_historico": regime_historico, "candidatas": list(candidatas.keys()), "sem_trade": [selected_strategy], "strategy_selection": selected_strategy}, {}
        candidatas = {selected_strategy: candidatas[selected_strategy]}
        logging.info("[STRATEGY_SELECTION] ciclo restrito a %s", selected_strategy)

    regime_dominante = max(regime_historico, key=regime_historico.get) if any(regime_historico.values()) else regime
    logging.info(
        "[FUNIL] Regime atual=%s | Regime dominante na janela=%s | Candidatas: %s",
        regime,
        regime_dominante,
        ", ".join(candidatas.keys()),
    )
    logging.info(f"[SIM][CANDIDATAS] {', '.join(candidatas.keys())}")

    def _identificar_direcao_estrategia(nome: str, lado_aprovado: str | None) -> str:
        n = (nome or "").upper()
        if n == "MEAN_REVERSION":
            return "buy"  # MR atual do projeto opera reversao de fundo (buy)
        if n in ("BREAKOUT", "EARLY_TREND") and lado_aprovado in ("buy", "sell"):
            return lado_aprovado  # estratÃ©gias direcionais obedecem o lado aprovado
        if n == "MARKET_MAKING_LATERAL":
            return "both"
        if n == "SWING_TREND_FOLLOWING" and lado_aprovado in ("buy", "sell"):
            return lado_aprovado
        if n == "PULLBACK_TREND_FOLLOWING" and lado_aprovado in ("buy", "sell"):
            return lado_aprovado
        return "both"

    allowed_display = []
    for nome in candidatas.keys():
        s = _identificar_direcao_estrategia(nome, lado_permitido)
        if lado_permitido in ("buy", "sell"):
            if s == lado_permitido:
                allowed_display.append(f"{nome.lower()}_{lado_permitido}")
        else:
            allowed_display.append(nome.lower())
    logging.info(
        f"[STRATEGY FILTER] Allowed Strategies = {', '.join(allowed_display) if allowed_display else 'NONE'}"
    )

    avaliacoes = {}
    sem_trade = set()
    resultados_brutos: dict[str, dict] = {}
    prioridade_status = {
        # Status de trades HISTORICOS do backtest; não representa ordem real.
        "backtest_com_trades": 3,
        "evidencia": 2,
        "sem_evidencia": 1,
    }

    def _extrair_metrica(res, nome_estrategia=None):
        if not res or not isinstance(res, dict):
            return None
        for chave in ["lucro_total", "lucro"]:
            if chave in res:
                valor = res[chave]
                if valor is not None:
                    return valor
        # BREAKOUT pode ter um setup atual confirmado sem possuir trades
        # historicos no recorte. Isso e evidencia analitica valida, mas nao
        # evidencia historica suficiente para liberar a execucao real.
        if (
            str(nome_estrategia or "").strip().upper() == "BREAKOUT"
            and bool(res.get("autorizado"))
            and str(res.get("direcao", "")).strip().lower() in ("buy", "sell")
        ):
            return 0.0
        return None

    def _trade_count(res: dict):
        """EvidÃªncia mÃ­nima de trade/sinal para permitir competir no ranking."""
        try:
            if "num_trades" in res and res.get("num_trades") is not None:
                return int(res.get("num_trades") or 0)
            trades = res.get("trades")
            if isinstance(trades, list):
                return len(trades)
            trade_log = res.get("trade_log")
            if isinstance(trade_log, list):
                return len(trade_log)
        except Exception:
            return None
        return None

    def _classificar_status_funil(nome: str, res: dict, n_trades):
        estrategia = str(nome or "").strip().lower()
        direcao = str((res or {}).get("direcao", "")).strip().lower()
        autorizado = bool((res or {}).get("autorizado", False))

        if estrategia == "breakout":
            if isinstance(n_trades, int) and n_trades > 0:
                return "backtest_com_trades"
            if autorizado and direcao in ("buy", "sell"):
                return "evidencia"
            return "sem_evidencia"

        if isinstance(n_trades, int) and n_trades > 0:
            return "backtest_com_trades"
        return "sem_evidencia"

    for nome, func in candidatas.items():
        strategy_side = _identificar_direcao_estrategia(nome, lado_permitido)
        if lado_permitido in ("buy", "sell"):
            if strategy_side in ("buy", "sell") and strategy_side != lado_permitido:
                logging.info(
                    "[STRATEGY FILTER] %s ignorada por conflito de direcao (%s != %s)",
                    nome,
                    strategy_side,
                    lado_permitido,
                )
                continue
            if strategy_side not in ("buy", "sell"):
                if nome == "MARKET_MAKING_LATERAL":
                    # Market making trabalha nos dois lados do book; não deve
                    # ser descartado só porque o gate direcional escolheu BUY
                    # ou SELL para as estratégias direcionais.
                    pass
                else:
                    logging.info(
                        "[STRATEGY FILTER] %s ignorada por direcao ambigua (%s) com approved_side=%s",
                        nome,
                        strategy_side,
                        lado_permitido,
                    )
                    continue

        try:
            logging.info(f"[SIM][BACKTEST][{nome}] Iniciando avalia??o.")
            resultado = func()
        except Exception as e:
            logging.error(f"[FUNIL] Erro ao avaliar {nome}: {e}")
            continue

        if isinstance(resultado, dict):
            resultado.setdefault("symbol", symbol_env or None)
            resultado = _aplicar_custos_realistas_backtest(resultado)
            resultado = _registrar_operacoes_por_regime(resultado)
            resultado = _anotar_trade_quality_backtest(df, resultado, regime)
            logging.info(
                "[SIM][REGIME-TRADES] estrategia=%s trades_por_regime=%s",
                nome,
                resultado.get("trades_por_regime", {}),
            )
            resultados_brutos[nome] = resultado
            logging.info(
                "[FUNIL RESULT] estrategia=%s direcao=%s autorizado=%s score=%s",
                nome,
                str(resultado.get("direcao", "NONE")).upper(),
                str(resultado.get("autorizado", "N/A")),
                str(resultado.get("score", resultado.get("score_calculado", "N/A"))),
            )
            side_result = str(resultado.get("direcao", "")).strip().lower()
            if (
                lado_permitido in ("buy", "sell")
                and nome != "MARKET_MAKING_LATERAL"
                and side_result in ("", "none", "auto", "both", "n/a", "na")
            ):
                sem_trade.add(nome)
                logging.warning(
                    f"[STRATEGY RESULT] {nome} -> {side_result.upper() if side_result else 'NONE'} | Resultado: DESCARTADO (direcao ambigua com approved_side={lado_permitido.upper()})"
                )
                continue
            if lado_permitido in ("buy", "sell") and side_result in ("buy", "sell") and side_result != lado_permitido:
                sem_trade.add(nome)
                logging.warning(
                    f"[STRATEGY RESULT] {nome} -> {side_result.upper()} | Resultado: DESCARTADO (direcao incompativel com approved_side={lado_permitido.upper()})"
                )
                continue
        else:
            # Compatibilidade defensiva: uma estratégia legada pode retornar
            # uma tupla/objeto diferente do contrato do funil. Isso não pode
            # derrubar o worker nem alcançar chamadas de .get().
            sem_trade.add(nome)
            logging.error(
                "[FUNIL] Resultado invalido de %s: tipo=%s; estrategia descartada sem evidencia.",
                nome,
                type(resultado).__name__,
            )
            continue
        metrica = _extrair_metrica(resultado, nome)
        n_trades = _trade_count(resultado) if isinstance(resultado, dict) else None
        status_funil = _classificar_status_funil(nome, resultado if isinstance(resultado, dict) else {}, n_trades)
        prioridade_funil = prioridade_status.get(status_funil, 1)

        if isinstance(resultado, dict):
            resultado["status_funil"] = status_funil
            resultado["prioridade_funil"] = prioridade_funil

        breakout_evidencia_atual = (
            str(nome).strip().upper() == "BREAKOUT"
            and status_funil == "evidencia"
            and bool(resultado.get("autorizado"))
            and str(resultado.get("direcao", "")).strip().lower() in ("buy", "sell")
        )
        # BREAKOUT com setup atual confirmado permanece no funil mesmo sem
        # trades historicos. O Macro/Risk/Execution Gate continuam decidindo
        # se a execucao real pode ocorrer.
        sem_trade_flag = (
            (bool(resultado.get("sem_trade")) and not breakout_evidencia_atual)
            or metrica is None
            or status_funil == "sem_evidencia"
        )
        if sem_trade_flag:
            sem_trade.add(nome)
            motivo_sem_trade = resultado.get("motivo") if isinstance(resultado, dict) else None
            logging.info(
                f"[FUNIL] {nome} excluÃ­da (sem_trade/sem_evidÃªncia). motivo={motivo_sem_trade} "
                f"trades={n_trades} metrica={metrica} status_funil={status_funil}"
            )
            logging.info(f"[SIM][BACKTEST][{nome}] sem_trade ou sem m?tricas.")
            continue

        avaliacoes[nome] = {
            "resultado": resultado,
            "metrica": metrica,
            "status_funil": status_funil,
            "prioridade_funil": prioridade_funil,
            "winrate": (
                resultado.get("winrate", resultado.get("win_rate", resultado.get("win_rate (%)", 0)))
                if isinstance(resultado, dict)
                else 0
            ),
        }
        logging.info(
            f"[FUNIL] {nome} avaliada | status_funil={status_funil} prioridade={prioridade_funil} "
            f"| m?trica={metrica} | winrate={avaliacoes[nome]['winrate']}"
        )
        logging.info(f"[SIM][BACKTEST][{nome}] m?tricas={metrica} winrate={avaliacoes[nome]['winrate']}")

    if not avaliacoes:
        logging.info("[FUNIL][NO-TRADE] Nenhuma estratÃ©gia vÃ¡lida; sem operaÃ§Ã£o.")
        logging.info("[SIM][FUNIL] Nenhuma vencedora (todas sem_trade ou erro).")
        return None, {"regime": regime, "candidatas": list(candidatas.keys()), "sem_trade": list(sem_trade)}, {}

    def _setup_probe(nome: str) -> dict:
        """Avalia setup AGORA (sem executar ordens), para evitar escolher vencedora sem sinal no candle atual.

        Retorna:
        - ok: bool
        - side: "buy"/"sell"/None
        - score: float (maior = melhor)
        - reason: str
        """
        try:
            side_allow = lado_permitido if lado_permitido in ("buy", "sell") else None

            if nome == "EARLY_TREND":
                from estrategias.early_trend.early_trend_core import detectar_entrada_early_trend

                df_ind = calculate_indicators(df.copy())
                entry = detectar_entrada_early_trend(df_ind, regime)
                if not getattr(entry, "ok", False) or not getattr(entry, "side", None):
                    return {"ok": False, "side": None, "score": 0.0, "reason": getattr(entry, "motivo", "sem_sinal")}
                if side_allow and entry.side != side_allow:
                    return {"ok": False, "side": entry.side, "score": 0.0, "reason": "lado_bloqueado"}

                score = 1.0
                try:
                    stop = float(entry.stop_loss) if entry.stop_loss is not None else None
                    tp = float(entry.take_profit) if entry.take_profit is not None else None
                    close = float(df_ind["close"].iloc[-1])
                    if stop is not None and tp is not None and close:
                        risk = abs(close - stop)
                        reward = abs(tp - close)
                        if risk > 0:
                            score = max(0.0, reward / risk)
                except Exception:
                    pass

                return {"ok": True, "side": entry.side, "score": float(score), "reason": getattr(entry, "motivo", "ok")}

            if nome == "BREAKOUT":
                # Usa o mesmo validador do breakout, mas no candle atual.
                # ObservaÃ§Ã£o: retorno Ã© booleano, entÃ£o score Ã© binÃ¡rio por enquanto.
                sides_to_try = [side_allow] if side_allow else ["buy", "sell"]
                for side in sides_to_try:
                    if not side:
                        continue
                    ok = bool(confirmar_entrada_com_criterios(df.copy(), side=side, is_backtest=False))
                    if ok:
                        return {"ok": True, "side": side, "score": 1.0, "reason": "confirmado"}
                return {"ok": False, "side": None, "score": 0.0, "reason": "sem_sinal"}

            if nome == "MEAN_REVERSION":
                # MR nova: detecta "fundo potencial" (arma stop-limit) sem side-effects de exchange.
                from datetime import datetime, timezone
                from estrategias.mean_reversion.mean_reversion_fsm import FSMConfig, MarketSnapshot, detect_fundo_potencial_setup

                cfg = FSMConfig(require_mr_regime=False)
                market = MarketSnapshot(
                    symbol=os.getenv("ATIVOS", "UNKNOWN"),
                    timeframe=str(os.getenv("INTERVAL", "1")),
                    now=datetime.now(timezone.utc),
                    df=df.copy(),
                    indicators={"regime": regime},
                )
                setup = detect_fundo_potencial_setup(market, cfg)
                if not setup:
                    return {"ok": False, "side": None, "score": 0.0, "reason": "sem_sinal"}
                # MR atual sÃ³ opera BUY (stop-limit de reversÃ£o de fundo).
                if side_allow and side_allow != "buy":
                    return {"ok": False, "side": "buy", "score": 0.0, "reason": "lado_bloqueado"}
                return {"ok": True, "side": "buy", "score": 1.0, "reason": "fundo_potencial"}

            if nome == "MARKET_MAKING_LATERAL":
                # Probe sem efeitos colaterais: valida a condição do candle
                # atual antes de permitir que o executor real publique ordens.
                # O livro real é consultado pelo executor; aqui usamos uma
                # estimativa conservadora baseada em ATR e OHLC.
                df_mm = calculate_indicators(df.copy()).bfill().ffill()
                if len(df_mm) < 30:
                    return {"ok": False, "side": "both", "score": 0.0, "reason": "dados_insuficientes"}

                regime_mm = int(RegimeDetector().detectar(df_mm, symbol=symbol_env or "DESCONHECIDO"))
                if regime_mm != 3:
                    return {
                        "ok": False,
                        "side": "both",
                        "score": 0.0,
                        "reason": f"regime_{regime_mm}_nao_lateral",
                    }

                close_mm = float(df_mm["close"].iloc[-1])
                atr_mm = float(df_mm["ATR"].iloc[-1]) if "ATR" in df_mm.columns else 0.0
                if close_mm <= 0 or atr_mm <= 0:
                    return {"ok": False, "side": "both", "score": 0.0, "reason": "atr_invalido"}

                atr_pct_mm = (atr_mm / close_mm) * 100.0
                atr_max_mm = max(float(os.getenv("MM_ATR_PCT_MAX", "0.08")), 0.0)
                if atr_pct_mm > atr_max_mm:
                    return {
                        "ok": False,
                        "side": "both",
                        "score": 0.0,
                        "reason": f"atr_acima_limite:{atr_pct_mm:.4f}%>{atr_max_mm:.4f}%",
                    }

                fator_mm = max(float(os.getenv("MM_FATOR_LATERALIDADE", "0.25")), 0.0)
                spread_min_pct = max(float(os.getenv("MM_SPREAD_MIN_PCT", "0.0001")), 0.0)
                spread_pct = max((atr_mm / close_mm) * fator_mm, spread_min_pct)

                maker_fee_pct = max(float(os.getenv("BACKTEST_MAKER_FEE_PCT_PER_SIDE", "0.0005")), 0.0)
                slippage_pct = max(float(os.getenv("BACKTEST_SLIPPAGE_PCT", "0.0002")), 0.0)
                round_trip_cost_pct = (2.0 * maker_fee_pct) + (2.0 * slippage_pct)
                min_edge_pct = max(float(os.getenv("MM_PROBE_MIN_EDGE_PCT", "0.0001")), 0.0)
                edge_pct = spread_pct - round_trip_cost_pct
                if edge_pct <= min_edge_pct:
                    return {
                        "ok": False,
                        "side": "both",
                        "score": 0.0,
                        "reason": (
                            f"spread_liquido_insuficiente:{edge_pct:.5f}%"
                            f"<=min:{min_edge_pct:.5f}%"
                        ),
                    }

                score_mm = min(1.0, max(0.0, edge_pct / max(spread_pct, 1e-12)))
                return {
                    "ok": True,
                    "side": "both",
                    "score": float(score_mm),
                    "reason": (
                        f"mm_probe_ok:regime=3 atr={atr_pct_mm:.4f}% "
                        f"spread={spread_pct:.5f}% edge={edge_pct:.5f}%"
                    ),
                }

            if nome == "SWING_TREND_FOLLOWING":
                if swing_timeframes is None:
                    return {"ok": False, "side": None, "score": 0.0, "reason": "timeframes_swing_ausentes"}
                swing_1h = swing_timeframes.get("1h")
                swing_4h = swing_timeframes.get("4h")
                swing_daily = swing_timeframes.get("1d")
                swing_regime = classificar_regime_swing(swing_4h, swing_daily)
                if swing_regime not in {1, 2}:
                    return {"ok": False, "side": None, "score": 0.0, "reason": f"regime_swing_{swing_regime}_bloqueado"}
                signal = (
                    avaliar_setup_entrada_1h(swing_1h, swing_4h, swing_daily, regime=swing_regime)
                    if os.getenv("SWING_ENTRY_TIMEFRAME", "1h").strip().lower() == "1h"
                    else avaliar_setup_swing_trend_following(swing_4h, swing_daily, regime=swing_regime)
                )
                if not signal.ok:
                    return {"ok": False, "side": signal.side, "score": signal.score, "reason": signal.reason}
                if side_allow and signal.side != side_allow:
                    return {"ok": False, "side": signal.side, "score": 0.0, "reason": "lado_bloqueado"}
                return {"ok": True, "side": signal.side, "score": signal.score, "reason": signal.reason}

            if nome == "PULLBACK_TREND_FOLLOWING":
                if swing_timeframes is None:
                    return {"ok": False, "side": None, "score": 0.0, "reason": "timeframes_pullback_ausentes"}
                one = swing_timeframes.get("1h")
                four = swing_timeframes.get("4h")
                daily = swing_timeframes.get("1d")
                pullback_regime = classificar_regime_swing(four, daily)
                if not _pullback_regime_allowed(pullback_regime):
                    return {"ok": False, "side": None, "score": 0.0, "reason": f"regime_pullback_{pullback_regime}_bloqueado"}
                signal = avaliar_setup_pullback_trend_following(one, four, daily, regime=pullback_regime)
                if not signal.ok:
                    return {"ok": False, "side": signal.side, "score": signal.score, "reason": signal.reason}
                if side_allow and signal.side != side_allow:
                    return {"ok": False, "side": signal.side, "score": 0.0, "reason": "lado_bloqueado"}
                return {"ok": True, "side": signal.side, "score": signal.score, "reason": signal.reason}

            # Outras estratÃ©gias: sem probe dedicado (nÃ£o participa do gate de setup).
            return {"ok": False, "side": side_allow, "score": 0.0, "reason": "sem_probe"}
        except Exception as e:
            return {"ok": False, "side": None, "score": 0.0, "reason": f"erro_probe:{e}"}

    # ====== SeleÃ§Ã£o em 2 estÃ¡gios ======
    # 1) Backtest -> ranking/peso
    # 2) Setup agora -> gate (evita escolher estratÃ©gia sem sinal no candle atual)
    setup_now: dict[str, dict] = {nome: _setup_probe(nome) for nome in candidatas.keys()}
    # Um probe positivo não basta se a estratégia foi descartada antes por
    # conflito de direção, erro ou ausência de métricas do backtest.
    setup_ok = {
        k: v for k, v in setup_now.items()
        if v.get("ok") and k in avaliacoes
    }

    def _rank01(values: dict[str, float]) -> dict[str, float]:
        items = [(k, float(v)) for k, v in values.items() if v is not None]
        if not items:
            return {k: 0.0 for k in values.keys()}
        ordered = sorted(items, key=lambda kv: kv[1])
        n = len(ordered)
        if n <= 1:
            return {k: 1.0 for k, _ in ordered}
        out: dict[str, float] = {k: 0.0 for k in values.keys()}
        for idx, (k, _) in enumerate(ordered):
            out[k] = float(idx) / float(n - 1)
        return out

    metrica_map = {k: float(v.get("metrica")) for k, v in avaliacoes.items() if v.get("metrica") is not None}
    metrica_rank = _rank01({k: metrica_map.get(k, 0.0) for k in candidatas.keys()})

    setup_weight = float(os.getenv("FUNIL_SETUP_WEIGHT", "1.0"))
    backtest_weight = float(os.getenv("FUNIL_BACKTEST_WEIGHT", "0.25"))

    if fixed_strategy_mode:
        # Worker live configurado para uma estrategia: nao existe ranking nem
        # escolha competitiva. O backtest historico da propria estrategia
        # continua sendo a validacao fixa; o setup atual e o unico gate de
        # entrada.
        if selected_strategy not in avaliacoes:
            for nome, s in setup_now.items():
                logging.info(
                    "[ESTRATEGIA FIXA][SETUP] %s ok=%s side=%s reason=%s score=%s",
                    nome, s.get("ok"), s.get("side"), s.get("reason"), s.get("score"),
                )
            logging.info(
                "[ESTRATEGIA FIXA][NO-TRADE] %s sem setup atual confirmado; "
                "nao ha selecao de outra estrategia.",
                selected_strategy,
            )
            return None, {
                "regime": regime,
                "regime_atual": regime,
                "regime_dominante": regime_dominante,
                "regime_historico": regime_historico,
                "candidatas": list(candidatas.keys()),
                "sem_trade": list(sem_trade),
                "setup_now": setup_now,
                "no_trade_reason": "sem_setup_atual",
                "strategy_selection": selected_strategy,
                "fixed_strategy": True,
            }, {}
        vencedor = selected_strategy
        logging.info(
            "[ESTRATEGIA FIXA] estrategia configurada=%s; ranking entre estrategias "
            "desativado; setup atual permanece gate de entrada (ok=%s).",
            vencedor,
            bool(setup_now.get(vencedor, {}).get("ok")),
        )
    elif setup_ok:
        scores_finais: dict[str, float] = {}
        for nome in setup_ok.keys():
            s = setup_now.get(nome, {})
            score_setup = float(s.get("score") or 0.0)
            score_backtest = float(metrica_rank.get(nome) or 0.0)
            prioridade = float((avaliacoes.get(nome, {}) or {}).get("prioridade_funil", 0))
            scores_finais[nome] = (setup_weight * score_setup) + (backtest_weight * score_backtest) + prioridade
            logging.info(
                f"[FUNIL][SETUP] {nome} ok={s.get('ok')} side={s.get('side')} reason={s.get('reason')} "
                f"| setup_score={score_setup:.4f} backtest_rank={score_backtest:.4f} prioridade={prioridade:.0f} "
                f"final={scores_finais[nome]:.4f}"
            )

        vencedor = max(scores_finais.items(), key=lambda kv: kv[1])[0]
        logging.info(f"[FUNIL] Vencedora (setup_now + backtest_rank): {vencedor}")
    else:
        # Sem setup atual não existe estratégia elegível para execução real.
        # O ranking histórico é apenas diagnóstico e não pode escolher uma
        # vencedora sem sinal no candle atual.
        for nome, s in setup_now.items():
            logging.info(f"[FUNIL][SETUP] {nome} ok={s.get('ok')} side={s.get('side')} reason={s.get('reason')} score={s.get('score')}")
        logging.info(
            "[FUNIL][NO-TRADE] Nenhum setup atual confirmado; ranking histórico não será usado como vencedora."
        )
        return None, {
            "regime": regime,
            "regime_atual": regime,
            "regime_dominante": regime_dominante,
            "regime_historico": regime_historico,
            "candidatas": list(candidatas.keys()),
            "sem_trade": list(sem_trade),
            "setup_now": setup_now,
            "no_trade_reason": "sem_setup_atual",
        }, {}

    avaliacao_vencedor = avaliacoes.get(vencedor) or {}
    resultado_vencedor = avaliacao_vencedor.get("resultado") or resultados_brutos.get(vencedor) or {}

    # Alguns casos (ex.: setup_now aprovou, mas backtest ficou "sem evidÃªncia") podem nÃ£o estar em `avaliacoes`.
    # NÃ£o pode crashar: usa defaults seguros.
    direcao_resultado = None
    if isinstance(resultado_vencedor, dict):
        direcao_resultado = resultado_vencedor.get("direcao", None)
    if not direcao_resultado:
        direcao_resultado = (setup_now.get(vencedor, {}) or {}).get("side") or lado_permitido

    retorno_pct = 0.0
    if isinstance(resultado_vencedor, dict):
        retorno_pct = resultado_vencedor.get("retorno_pct", None)

    if retorno_pct is None:
        retorno_pct = 0.0

    if not retorno_pct:
        saldo_inicial_calc = (
            resultado_vencedor.get("saldo_inicial", capital_inicial)
            if isinstance(resultado_vencedor, dict)
            else capital_inicial
        )
        lucro_total_calc = (
            resultado_vencedor.get("lucro_total", resultado_vencedor.get("lucro", 0.0))
            if isinstance(resultado_vencedor, dict)
            else 0.0
        )
        if saldo_inicial_calc:
            retorno_pct = float(lucro_total_calc / saldo_inicial_calc) * 100.0

    metrica_vencedor = avaliacao_vencedor.get("metrica")
    if metrica_vencedor is None and isinstance(resultado_vencedor, dict):
        metrica_vencedor = _extrair_metrica(resultado_vencedor)
    if metrica_vencedor is None:
        metrica_vencedor = 0.0

    winrate_vencedor = avaliacao_vencedor.get("winrate")
    if winrate_vencedor is None and isinstance(resultado_vencedor, dict):
        winrate_vencedor = resultado_vencedor.get(
            "winrate",
            resultado_vencedor.get("win_rate", resultado_vencedor.get("win_rate (%)", 0)),
        ) or 0
    if winrate_vencedor is None:
        winrate_vencedor = 0.0

    score_raw = None
    score_pct_raw = None
    score_source = "ausente"
    if isinstance(resultado_vencedor, dict):
        if resultado_vencedor.get("score", None) is not None:
            score_raw = resultado_vencedor.get("score")
            score_source = "score"
        elif resultado_vencedor.get("score_calculado", None) is not None:
            score_raw = resultado_vencedor.get("score_calculado")
            score_source = "score_calculado"
        if resultado_vencedor.get("score_pct", None) is not None:
            score_pct_raw = resultado_vencedor.get("score_pct")

    # Preserva o resultado do probe do setup atual.  Ele é diferente da
    # quantidade de operações do backtest: um setup confirmado pode ter zero
    # trades históricos na janela curta e ainda assim estar válido agora.
    setup_vencedor = setup_now.get(vencedor, {}) if isinstance(setup_now, dict) else {}
    setup_vencedor = setup_vencedor if isinstance(setup_vencedor, dict) else {}

    historical_validation = (
        resultado_vencedor.get("historical_validation")
        if isinstance(resultado_vencedor, dict)
        else None
    )
    backtest_valid_raw = (
        resultado_vencedor.get("backtest_valid")
        if isinstance(resultado_vencedor, dict)
        else False
    )
    # Compatibilidade defensiva: alguns resultados antigos não carregam
    # backtest_valid, embora tragam a validação histórica já aprovada.
    if backtest_valid_raw is None:
        backtest_valid_raw = (
            bool(historical_validation.get("valid"))
            if isinstance(historical_validation, dict)
            else False
        )

    melhor = {
        "estrategia": vencedor,
        "direcao": direcao_resultado.upper() if direcao_resultado else "AUTO",
        "autorizado": contexto_decisao["autorizado"],
        "confianca": contexto_decisao["confianca"],
        "lucro_total": metrica_vencedor,
        "retorno_pct": retorno_pct,
        "saldo_inicial": resultado_vencedor.get("saldo_inicial", capital_inicial) if isinstance(resultado_vencedor, dict) else capital_inicial,
        "saldo_final": resultado_vencedor.get("saldo_final", resultado_vencedor.get("saldo_inicial", capital_inicial)) if isinstance(resultado_vencedor, dict) else capital_inicial,
        "trade_log": resultado_vencedor.get("trade_log", []) if isinstance(resultado_vencedor, dict) else [],
        "trades_por_regime": resultado_vencedor.get("trades_por_regime", {}) if isinstance(resultado_vencedor, dict) else {},
        "winrate": winrate_vencedor,
        "num_trades": int(
            resultado_vencedor.get(
                "num_trades",
                len(resultado_vencedor.get("trade_log", [])),
            )
        ) if isinstance(resultado_vencedor, dict) else 0,
        "status_funil": avaliacao_vencedor.get("status_funil", "sem_evidencia"),
        "prioridade_funil": int(avaliacao_vencedor.get("prioridade_funil", 1) or 1),
        "score": (float(score_raw) if score_raw is not None else None),
        "score_pct": (float(score_pct_raw) if score_pct_raw is not None else None),
        "score_source": score_source,
        "setup_now_ok": bool(setup_vencedor.get("ok", False)),
        "setup_now_side": setup_vencedor.get("side"),
        "setup_now_reason": setup_vencedor.get("reason", "nao_informado"),
        "backtest_valid": bool(backtest_valid_raw),
        "historical_validation": historical_validation,
    }

    resultados = {
        "candidatas": list(candidatas.keys()),
        "sem_trade": list(sem_trade),
        "regime": regime,
        "regime_atual": regime,
        "regime_dominante": regime_dominante,
        "regime_historico": regime_historico,
        "contexto_decisao": contexto_decisao,
        "setup_now": setup_now,
    }

    relatorios_por_estrategia = {vencedor: (resultado_vencedor.get("relatorio_completo", "") if isinstance(resultado_vencedor, dict) else "")}

    logging.info(f"[FUNIL] Estrat?gia vencedora: {vencedor}")
    logging.info(f"[SIM][FUNIL] Vencedora={vencedor} | lucro={float(metrica_vencedor):.6f} | winrate={float(winrate_vencedor):.2f}")

    return melhor, resultados, relatorios_por_estrategia








if __name__ == "__main__":
    import time
    import logging
    from datetime import datetime
    from utils.indicadores import calculate_indicators
    from backtest_module import carregar_dados_historicos

     # âœ… EstratÃ©gias para execuÃ§Ã£o real com nomes corretos
    from estrategia_entrada.estrategia_breakout_filtros import _breakout
    from estrategia_entrada.Market_Making._marketmaking_execucao import executar_marketmaking_realtime
    from estrategia_entrada.estrategia_mean_reversion import executar_mean_reversion_realtime
    from utils.parse_env import get_ativos_env
    import os
    symbol = get_ativos_env()[0]  # Sempre pega o primeiro da lista de ativos
    interval = os.getenv("INTERVAL", "1")

    # ExecuÃ§Ã£o manual pode usar a exchange ativa; importaÃ§Ã£o do mÃ³dulo nÃ£o cria cliente.
    session = create_exchange_client()

    #symbol = "BTCUSDT"
    #interval = "1"

    df = carregar_dados_historicos(session, symbol, interval)

    if df is None or df.empty:
        logging.error("âŒ Erro ao carregar dados histÃ³ricos.")
        exit()

    # âœ… ConversÃ£o segura de timestamp
    def parse_timestamp_safe(valor):
        if isinstance(valor, datetime):
            return valor
        if isinstance(valor, str) and "T" in valor:
            return datetime.fromisoformat(valor)
        if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.isdigit()):
            return datetime.utcfromtimestamp(int(valor) / 1000)
        raise ValueError(f"Formato invÃ¡lido para timestamp: {valor}")

    df["time"] = df["time"].apply(parse_timestamp_safe)

    df_ind = calculate_indicators(df.copy())
    if df_ind is None or not isinstance(df_ind, pd.DataFrame):
        logging.error("âŒ Indicadores invÃ¡lidos.")
        exit()
    df = df_ind

    vol = calcular_volatilidade(df.copy())
    if vol is None or not isinstance(vol, (int, float)) or pd.isna(vol) or vol <= 0:
        logging.error(f"âŒ Volatilidade invÃ¡lida: {vol}")
        exit()
    CAPITAL_INICIAL = 10000  # ou qualquer valor desejado
    sentimento = "neutro"  # fallback seguro

    melhor, resultados, relatorios = executar_backtest_completo(
        df,
        sentimento=sentimento,
        capital_inicial=CAPITAL_INICIAL
    )




    if not melhor or not isinstance(melhor, dict):
        logging.warning("âš ï¸ Nenhuma estratÃ©gia foi aprovada ou houve empate no backtest.")
        #logging.warning(f"ðŸ“Š RelatÃ³rio completo do backtest: {relatorio}")
        logging.warning(f"âš ï¸ Resultado do backtest invÃ¡lido ou inesperado: {melhor}")
        time.sleep(5)
        exit()

    # âœ… Exibir resultado da melhor estratÃ©gia
    logging.info("\nðŸ“Š Resultado Final:")
    logging.info(f" EstratÃ©gia: {melhor['estrategia']}")
    logging.info(f" DireÃ§Ã£o:   {melhor['direcao']}")
    logging.info(f" Lucro:     {melhor.get('lucro_total', 0):.2f}")
    logging.info(f" Retorno:   {melhor.get('retorno_pct', 0)}%")

    # ================= TRADE AUTHORIZATION GATE =================

    estrategias_execucao = {
        "BREAKOUT": lambda: _breakout(symbol, session, session),
        "MARKET_MAKING_LATERAL": lambda: executar_market_making(
            session=session, symbol=symbol, df=df, regime_atual=regime_exec
        ),
        "MEAN_REVERSION": lambda: executar_mean_reversion_realtime(
            session=session, symbol=symbol
        ),
    }

    estrategia_escolhida = melhor["estrategia"].upper()

    try:
        regime_exec = RegimeDetector().detectar(df, symbol=symbol)
    except Exception:
        regime_exec = 2

    indicadores_gate = df.iloc[-1].to_dict()
    side_gate = (melhor.get("direcao") or "").upper()
    score_gate = melhor.get("confianca", 0)

    autorizacao = autorizar_trade(
        df=df,
        regime=regime_exec,
        side=side_gate,
        score_side=score_gate,
        indicadores=indicadores_gate,
        estrategia_escolhida=estrategia_escolhida,
        modo_execucao=os.getenv("MODO_EXECUCAO", "PROD"),
    )

    if not autorizacao.get("autorizado"):
        logging.warning(f"[AUTH] ExecuÃ§Ã£o bloqueada: {autorizacao.get('motivo')}")
        logging.info(f"[SIM][GATE] autorizado=False motivo={autorizacao.get('motivo')}")
        exit()  # encerra execuÃ§Ã£o com seguranÃ§a

    logging.info(
        f"[AUTH] AUTORIZADO | EstratÃ©gia={estrategia_escolhida} | Side={side_gate}"
    )
    logging.info(f"[SIM][GATE] autorizado=True estrategia={estrategia_escolhida} side={side_gate}")

    if os.getenv("MODO_EXECUCAO", "PROD").strip().upper() == "SIMULACAO":
        logging.info(f"[SIM][EXECUCAO] ExecuÃ§Ã£o simulada | EstratÃ©gia={estrategia_escolhida} | Side={side_gate}")
        exit()  # encerra execuÃ§Ã£o em modo simulaÃ§Ã£o

    if estrategia_escolhida in estrategias_execucao:
        estrategias_execucao[estrategia_escolhida]()
    else:
        logging.warning(
            f"[AUTH] EstratÃ©gia '{estrategia_escolhida}' nÃ£o encontrada."
        )
