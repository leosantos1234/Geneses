import logging
logger = logging.getLogger(__name__)

import time
import os
import threading
import json
import uuid
import math
from datetime import datetime
from compra1 import session
from utils.indicadores import calculate_indicators
from api_handlers.market_data import get_market_data_bybit
from banco_trade.db_trades import salvar_trade_sqlite, atualizar_trade_sqlite
from gestor_de_trade import (
    ajustar_trailing_stop_condicional,
    avaliar_score_tecnico,
    detectar_reversao,
    calcular_lucro_e_registrar,
    parse_timestamp_safe,
)
from utils.logica_stop_take_adaptativo import calcular_stop_take_adaptativo
from utils.utils_telegram import send_telegram_message  # se já não estiver importado
from utils.telegram_trade_notifications import notify_swing_entry, notify_swing_exit
from utils.trade_transition_manager import avaliar_trade_breakout, detectar_regime_mercado, detectar_tendencia
from feedback.feedback_trade import registrar_trade_fechado
from trade_quality.trade_classifier import registrar_trade_quality_fechado



MODO_GESTAO = os.getenv("MODO_GESTAO", "CORRETORA").upper()
# CORRETORA ou ROBO

# Fração de ATR permitida contra a entrada durante imunidade do BREAKOUT
BREAKOUT_IMUNIDADE_ATR_FRAC = 0.25
# Candles de imunidade pós-entrada (sem saídas antecipadas)
BREAKOUT_IMUNIDADE_CANDLES = int(os.getenv("BREAKOUT_IMUNIDADE_CANDLES", "2"))

# Time-stop para BREAKOUT em estado AGUARDAR
BREAKOUT_TIME_STOP_CICLOS = 12
BREAKOUT_MIN_MOVE_ATR_FRAC = 0.30


# ==========================
# ESTADO PERSISTENTE DA OPERAÇÃO
# ==========================
trade_state = {
    "sl_inicial": None,
    "tp_inicial": None,
    "congelado": False,
    "sl_congelado_corretora": False,
    "parcial_executada": False,
    "fase": "entrada",
    # 🧱 Governança: "Stop só anda quando a estrutura anda"
    # - BUY confirma estrutura ao romper o último topo estrutural relevante (pré-entrada)
    # - SELL confirma estrutura ao romper o último fundo estrutural relevante (pré-entrada)
    "estrutura_confirmada": False,
    "estrutura_nivel": None,
    "estrutura_tipo": None,
    "estrutura_lookback": None,
}


def validar_atr_para_trailing(indicators, symbol: str, strategy: str):
    """Valida ATR para uso em trailing/BE e aplica fail-safe."""
    atr_valor_monitor = None
    allow_trailing_flag = True

    if isinstance(indicators, dict):
        allow_trailing_raw = indicators.get("ALLOW_TRAILING", 1)
        allow_trailing_flag = str(allow_trailing_raw).strip().lower() not in ("0", "false", "no")

        atr_raw = indicators.get("ATR")
        try:
            if atr_raw is None:
                raise ValueError("ATR ausente")
            atr_val = float(atr_raw)
            if atr_val <= 0 or math.isnan(atr_val):
                raise ValueError(f"ATR inválido={atr_raw}")
            atr_valor_monitor = atr_val
        except Exception as e:
            logger.error(
                f"🛑 FAIL-SAFE: trailing/BE bloqueados por ATR ausente/inválido | "
                f"symbol={symbol} | strategy={strategy} | motivo={e} | "
                f"keys={list(indicators.keys())}"
            )
    else:
        logger.error(
            f"🛑 FAIL-SAFE: indicators inválido (tipo={type(indicators).__name__}) | "
            f"symbol={symbol} | strategy={strategy}. trailing/BE bloqueados."
        )

    trailing_be_liberado = (atr_valor_monitor is not None) and allow_trailing_flag
    if not allow_trailing_flag:
        logger.error(
            f"🛑 FAIL-SAFE: trailing/BE bloqueados por ALLOW_TRAILING=0 | "
            f"symbol={symbol} | strategy={strategy}"
        )

    return atr_valor_monitor, allow_trailing_flag, trailing_be_liberado


def _atr_fallback_dos_candles(df, janela=14):
    """Obtém um ATR simples dos candles quando o indicador de entrada veio zerado."""
    try:
        if df is None or df.empty:
            return None
        valores = []
        fechamento_anterior = None
        for linha in df.tail(max(int(janela) + 1, 20)).itertuples(index=False):
            maxima = float(getattr(linha, "high"))
            minima = float(getattr(linha, "low"))
            fechamento = float(getattr(linha, "close"))
            if fechamento_anterior is None:
                true_range = maxima - minima
            else:
                true_range = max(
                    maxima - minima,
                    abs(maxima - fechamento_anterior),
                    abs(minima - fechamento_anterior),
                )
            if math.isfinite(true_range) and true_range > 0:
                valores.append(true_range)
            fechamento_anterior = fechamento
        if not valores:
            return None
        atr = sum(valores[-int(janela):]) / min(len(valores), int(janela))
        return atr if math.isfinite(atr) and atr > 0 else None
    except Exception as exc:
        logger.warning(f"⚠️ Falha no fallback de ATR dos candles: {exc}")
        return None


def _strategy_allows_tp_none(strategy: str | None) -> bool:
    """
    Estratégias que podem operar sem TP fixo (saída via trailing/gestão).
    Configurável por env: TP_NONE_STRATEGIES="breakout,continuation_trade,bybit_existing"
    """
    if os.getenv("KEEP_TP_ALWAYS", "1").strip().lower() in ("1", "true", "yes"):
        return False
    strategy_norm = str(strategy or "").strip().lower()
    raw = os.getenv("TP_NONE_STRATEGIES", "breakout,continuation_trade,bybit_existing")
    allowed = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return strategy_norm in allowed


def _get_trades_db_path() -> str:
    try:
        from banco_trade import db_trades
        return getattr(db_trades, "DB_PATH", "trades.db")
    except Exception:
        return "trades.db"


def _trade_exists(trade_id: str) -> bool:
    if not trade_id:
        return False
    try:
        from genesis_runtime import connect_operational_database
        conn = connect_operational_database(read_only=True)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM trades WHERE trade_id=%s LIMIT 1", (trade_id,))
        found = cur.fetchone() is not None
        conn.close()
        return found
    except Exception:
        return False


def _insert_trade_if_missing(
    *,
    trade_id: str,
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float | None = None,
    profit_loss: float | None = None,
    strategy: str | None = None,
    risk_parameters: dict | None = None,
    indicators: dict | None = None,
    regime: str | int | None = None,
    tendencia: str | None = None,
    consenso: str | None = None,
    consenso_score: float | None = None,
    consenso_razao: str | None = None,
    decisao_lstm: str | None = None,
) -> bool:
    if _trade_exists(trade_id):
        return False
    try:
        params = risk_parameters or {}
        indicators_data = indicators or {}

        def _get_num(value):
            try:
                return float(value)
            except Exception:
                return None

        now_iso = datetime.utcnow().isoformat()
        trailing_seed = params.get("trailing_stop")
        if trailing_seed in (None, "", 0, "0", "0.0"):
            trailing_seed = params.get("trailing_offset")

        entry_score = _get_num(
            indicators_data.get("entry_score")
            or indicators_data.get("score")
            or params.get("entry_score")
        )
        consenso_score_value = (
            consenso_score
            if consenso_score is not None
            else params.get("consenso_score")
        )
        consenso_score_value = _get_num(consenso_score_value)
        atr_entrada_value = _get_num(
            indicators_data.get("ATR")
            or indicators_data.get("atr")
            or indicators_data.get("atr_entrada")
            or params.get("atr_entrada")
        )

        trade_entry = {
            "trade_id": trade_id,
            "timestamp": now_iso,
            "symbol": symbol,
            "side": (side or "").lower() or None,
            "entry_price": float(entry_price) if entry_price is not None else None,
            "exit_price": float(exit_price) if exit_price is not None else None,
            "profit_loss": float(profit_loss) if profit_loss is not None else None,
            "strategy": strategy,
            "estrategia_utilizada": strategy,
            "stop_loss": params.get("stop_loss"),
            "take_profit": params.get("take_profit"),
            "trailing_stop": trailing_seed,
            "trailing_stop_inicial": trailing_seed,
            "decision_source": "monitoramento",
            "entry_score": 0.0 if entry_score is None else entry_score,
            "rsi_entrada": _get_num(indicators_data.get("RSI") or indicators_data.get("rsi")),
            "atr_entrada": 0.0 if atr_entrada_value is None else atr_entrada_value,
            "volume_entrada": _get_num(indicators_data.get("volume") or indicators_data.get("VOLUME")),
            "regime": regime if regime is not None else params.get("regime"),
            "tendencia": tendencia if tendencia is not None else params.get("tendencia"),
            "consenso": consenso if consenso is not None else params.get("consenso"),
            "consenso_score": 0.0 if consenso_score_value is None else consenso_score_value,
            "consenso_razao": (
                consenso_razao if consenso_razao is not None else params.get("consenso_razao")
            ),
            "decisao_lstm": (
                decisao_lstm if decisao_lstm is not None else params.get("decisao_lstm")
            ),
            "timestamp_decisao_ia": params.get("timestamp_decisao_ia") or now_iso,
            "open_ts": params.get("open_ts") or now_iso,
            "close_ts": now_iso if (exit_price is not None or profit_loss is not None) else None,
            "status": "fechado" if (exit_price is not None or profit_loss is not None) else "aberto",
        }
        salvar_trade_sqlite(trade_entry)
        logging.info("Trade inserido no banco pelo monitoramento.")
        return True
    except Exception as e:
        logging.warning(f"Falha ao inserir trade no banco: {e}")
        return False


def _cooldown_breakout_path(symbol: str) -> str:
    return f"cooldown_breakout_{symbol}.json"


def _load_breakout_cooldown(symbol: str) -> dict:
    try:
        path = _cooldown_breakout_path(symbol)
        if not os.path.exists(path):
            return {"loss_streak": 0, "cooldown_until": 0.0}
        with open(path, "r") as f:
            data = json.load(f) or {}
        return {
            "loss_streak": int(data.get("loss_streak", 0) or 0),
            "cooldown_until": float(data.get("cooldown_until", 0) or 0.0),
        }
    except Exception:
        return {"loss_streak": 0, "cooldown_until": 0.0}


def _save_breakout_cooldown(symbol: str, loss_streak: int, cooldown_until: float) -> None:
    try:
        path = _cooldown_breakout_path(symbol)
        with open(path, "w") as f:
            json.dump({"loss_streak": loss_streak, "cooldown_until": cooldown_until}, f)
    except Exception as e:
        logger.warning(f"[BREAKOUT] Falha ao salvar cooldown: {e}")


def _aplicar_cooldown_breakout(symbol: str, entry_price: float, last_price: float, side: str, strategy: str) -> None:
    if strategy != "breakout":
        return
    cooldown_losses = int(os.getenv("COOLDOWN_LOSSES", "0") or 0)
    cooldown_minutes = int(os.getenv("COOLDOWN_MINUTES", "0") or 0)
    if cooldown_losses <= 0 or cooldown_minutes <= 0:
        return
    if last_price is None:
        return

    pnl = (last_price - entry_price) if side == "buy" else (entry_price - last_price)
    state = _load_breakout_cooldown(symbol)
    loss_streak = int(state.get("loss_streak", 0) or 0)
    cooldown_until_prev = float(state.get("cooldown_until", 0) or 0.0)

    if pnl < 0:
        loss_streak += 1
        if loss_streak >= cooldown_losses:
            cooldown_until = max(cooldown_until_prev, time.time() + (cooldown_minutes * 60))
            _save_breakout_cooldown(symbol, loss_streak, cooldown_until)
            logger.warning(
                f"[BREAKOUT] Cooldown ativado para {symbol} ({cooldown_minutes} min) "
                f"após {loss_streak} perdas."
            )
            return
        _save_breakout_cooldown(symbol, loss_streak, cooldown_until_prev)
        return

    _save_breakout_cooldown(symbol, 0, 0.0)


# =========================
# ⚙️ Ajuste de quantidade mínima
# =========================
def ajustar_quantidade_para_minimo(session, symbol, qty, min_qty_default=0.001, step_default=0.001):
    """
    Ajusta a quantidade de contrato para respeitar os mínimos e steps da Bybit.
    - Consulta a API para obter minOrderQty e qtyStep.
    - Se a consulta falhar, usa valores padrão seguros (0.001).
    Evita rejeição de ordens por quantidade inválida.
    """
    try:
        # 🧩 Tenta consultar a Bybit
        info = session.get_instruments_info(category="linear", symbol=symbol)
        if info.get("retCode") != 0:
            logging.warning(f"⚠️ Falha ao consultar min_qty para {symbol}. Usando defaults.")
            min_qty = min_qty_default
            step_size = step_default
        else:
            dados = info["result"]["list"][0]["lotSizeFilter"]
            min_qty = float(dados.get("minOrderQty", min_qty_default))
            step_size = float(dados.get("qtyStep", step_default))

        # 🔹 Ajusta para o mínimo
        if qty < min_qty:
            logging.warning(f"⚠️ Qty {qty} abaixo do mínimo {min_qty}. Ajustando automaticamente.")
            qty = min_qty

        # 🔹 Arredonda para múltiplo do step permitido
        resto = qty % step_size
        if resto != 0:
            qty = round(qty - resto + step_size, 6)

        return round(qty, 6)

    except Exception as e:
        logging.error(f"❌ Falha ao ajustar quantidade mínima: {e}")
        # Fallback robusto — evita crash
        try:
            if qty < min_qty_default:
                qty = min_qty_default
            resto = qty % step_default
            if resto != 0:
                qty = round(qty - resto + step_default, 6)
            return round(qty, 6)
        except Exception:
            return round(qty, 6)



# =========================
# 🔍 Breakout: suporte auxiliar
# =========================
# =========================
# Helpers: filtros de lote/tick (Bybit) e quantização sem inflar qty
# =========================
def _get_symbol_filters(session, symbol):
    """
    Busca filtros de lot/tick da Bybit para um symbol (linear).
    Retorna (min_qty, qty_step, tick_size). Pode retornar None em fallback.
    """
    try:
        info = session.get_instruments_info(category="linear", symbol=symbol)
        if info.get("retCode") != 0:
            return None, None, None
        inst = (info.get("result") or {}).get("list") or []
        if not inst:
            return None, None, None
        inst0 = inst[0]
        lot = inst0.get("lotSizeFilter") or {}
        price = inst0.get("priceFilter") or {}
        min_qty = float(lot.get("minOrderQty")) if lot.get("minOrderQty") not in (None, "", "0") else None
        qty_step = float(lot.get("qtyStep")) if lot.get("qtyStep") not in (None, "", "0") else None
        tick_size = float(price.get("tickSize")) if price.get("tickSize") not in (None, "", "0") else None
        return min_qty, qty_step, tick_size
    except Exception:
        return None, None, None


def ajustar_quantidade_para_step_down(session, symbol, qty, min_qty_default=0.001, step_default=0.001):
    """
    Ajusta qty para baixo no múltiplo do step (não aumenta qty).
    Útil para parciais: evita distorcer 30% -> 50% por causa do step mínimo.
    """
    try:
        min_qty, step_size, _ = _get_symbol_filters(session, symbol)
        min_qty = float(min_qty) if min_qty else float(min_qty_default)
        step_size = float(step_size) if step_size else float(step_default)

        qty_f = float(qty)
        if qty_f <= 0:
            return 0.0

        steps = int(qty_f / step_size)  # floor
        qty_down = round(steps * step_size, 6)
        if qty_down < min_qty:
            return 0.0
        return qty_down
    except Exception:
        return 0.0


def ajustar_preco_para_tick(session, symbol, price, default_decimals=6):
    """
    Ajusta preço para o tickSize (arredondamento para múltiplo do tick).
    """
    try:
        _, _, tick = _get_symbol_filters(session, symbol)
        p = float(price)
        if not tick or tick <= 0:
            return round(p, default_decimals)
        ticks = int(p / tick)
        return round(ticks * tick, default_decimals)
    except Exception:
        try:
            return round(float(price), default_decimals)
        except Exception:
            return price


def _ultimo_pivo_relevante(df, side: str, lookback: int = 20):
    """
    Retorna o último topo/fundo relevante dentro da janela de lookback (exclui o candle atual).
    Simples: usa min/max de lows/highs da janela anterior. Evita overfitting e ruído intrabar.
    """
    try:
        janela = df.tail(lookback + 1).iloc[:-1]
        if janela.empty:
            return None
        if side.lower() == "buy":
            return float(janela["low"].min())
        return float(janela["high"].max())
    except Exception as e:
        logger.warning(f"⚠️ Falha ao calcular pivô relevante: {e}")
        return None


def _breakout_invalido(side: str, last_close: float, entry_price: float, pivo: float | None) -> bool:
    """
    Invalida??o estrutural do breakout (p?s-imunidade).

    Regra: trade negativo (pullback abaixo/acima da entrada) N?O invalida sozinho.

    - BUY: invalida apenas se fechar abaixo do piv? (?ltimo fundo relevante).
    - SELL: invalida apenas se fechar acima do piv? (?ltimo topo relevante).
    """
    try:
        if pivo is None:
            return False
        if side.lower() == "buy":
            return float(last_close) < float(pivo)
        return float(last_close) > float(pivo)
    except Exception as e:
        logger.warning(f"?? Erro ao avaliar invalida??o do breakout: {e}")
        return False

class OrdemUtils:
    @staticmethod
    def get_last_filled_order_id(api, symbol):
        """
        Retorna o ultimo orderId com status preenchido no historico.
        """
        try:
            res = api.get_order_history(category="linear", symbol=symbol, limit=20)
            if res.get("retCode") == 0 and "list" in res.get("result", {}):
                for order in res["result"]["list"]:
                    status = str(order.get("orderStatus") or "").lower()
                    avg_price = float(order.get("avgPrice") or 0)
                    if status in ("filled", "partiallyfilled") and avg_price > 0:
                        order_id = order.get("orderId")
                        if order_id:
                            logging.info(f"OrderId preenchido encontrado: {order_id}")
                            return str(order_id)
        except Exception as e:
            logging.error(f"Erro ao buscar order_id preenchido: {e}")
        return None

    @staticmethod
    def get_entry_price(symbol, order_id=None, fallback=None):
        """
        Recupera o entry price REAL da Bybit.
        Blindado contra valores None, vazio ou 0.0.
        """
        try:
            # 1️⃣ -> PRIMEIRA FONTE: histórico da ordem
            res = session.get_order_history(category="linear", symbol=symbol, limit=20)
            if res.get("retCode") == 0:
                lista = res["result"].get("list", [])
                for order in lista:
                    if order_id and str(order.get("orderId")) != str(order_id):
                        continue

                    price_str = order.get("avgPrice")
                    if price_str not in [None, "", "0", 0, "0.0", "0.00"]:
                        price = float(price_str)
                        if price > 0:
                            logging.info(f"✅ Entry price recuperado via histórico: {price}")
                            return price

            # 2️⃣ -> SEGUNDA FONTE: posição aberta
            pos = session.get_positions(category="linear", symbol=symbol)
            if pos.get("retCode") == 0:
                lista = pos["result"].get("list", [])
                if lista:
                    price_str = lista[0].get("entryPrice")
                    if price_str not in [None, "", "0", 0, "0.0", "0.00"]:
                        price = float(price_str)
                        if price > 0:
                            logging.info(f"⚠️ Entry price recuperado da posição: {price}")
                            return price

        except Exception as e:
            logging.warning(f"⚠️ Erro ao recuperar entry_price: {e}")

        # 3️⃣ -> FALLBACK SEGURO
        if fallback and fallback > 0:
            logging.warning(f"⚠️ Fallback utilizado para entry_price: {fallback}")
            return fallback

        # 4️⃣ -> ÚLTIMO RECURSO: mantém SL/TP vivos
        logging.error(f"❌ Entry price não encontrado — usando fallback emergencial.")
        return 1  # evita SL=0, TP=0 e destruição da posição



# =========================
# 📊 Parâmetros padrão de risco
# =========================
PARAMS = {
    "atr_min": float(os.getenv("ATR_MIN", 150)),
    "atr_mult": float(os.getenv("ATR_MULT", 0.9)),
    "sl_pct_min": float(os.getenv("SL_PCT_MIN", 0.006)),
    "sl_pct_max": float(os.getenv("SL_PCT_MAX", 0.012)),
    "rrr": float(os.getenv("RRR", 1.8)),
    "bb_dist_min": float(os.getenv("BB_DIST_MIN", 30)),
    "bb_mult": float(os.getenv("BB_MULT", 1.05)),
    "fixo_pct": float(os.getenv("FIXO_PCT", 0.0035)),
}


# =========================
# 🔄 Variáveis globais
# =========================
trailing_offset = None



def calcular_trailing_adaptativo(atr, tipo_stop, contexto, params=None):
    """
    Trailing stop adaptativo: responde à volatilidade (ATR), tipo de stop e contexto detectado.
    - atr: valor do ATR (float)
    - tipo_stop: tipo selecionado (ATR, bollinger, percentual, etc)
    - contexto: contexto de mercado ('volatilidade_alta', 'lateral', etc)
    - params: dict com hiperparâmetros opcionais
    Retorna o valor do trailing stop (em pontos).
    """



    # Use os mesmos parâmetros globais (PARAMS) se existirem
    p = {**PARAMS, **(params or {})} if 'PARAMS' in globals() else {}
    try:
        if contexto == "volatilidade_alta":
            trailing = max(atr * 1.3, p.get("bb_dist_min", 40) * 1.2)
        elif contexto == "lateral":
            trailing = max(atr * 0.7, p.get("bb_dist_min", 40) * 0.9)
        elif contexto in ["bollinger", "range_bollinger"]:
            trailing = max(atr * 1.0, p.get("bb_dist_min", 40))
        elif contexto == "tendencia_direcional":
            trailing = max(atr * 1.5, p.get("bb_dist_min", 40) * 1.3)
        elif tipo_stop == "percentual":
            trailing = max(atr, p.get("fixo_pct", 0.004) * 100000)
        else:
            trailing = max(atr * 0.9, p.get("bb_dist_min", 40))
        trailing = max(trailing, 20)  # Nunca menor que 20
        return float(round(trailing, 2))
    except Exception as e:
        logging.error(f"Erro no calcular_trailing_adaptativo: {e}", exc_info=True)
        return float(round(atr, 2))



def get_lock_path(symbol):
    return f"ordem_ativa_{symbol}.flag"

def remove_lock_fisico(symbol):
    lock_path = get_lock_path(symbol)
    if os.path.exists(lock_path):
        os.remove(lock_path)  

def ordem_ainda_aberta(api, symbol):
    """
    Verifica se ainda há uma posição aberta no símbolo especificado.
    Retorna True se houver posição aberta com tamanho > 0.
    Retorna False caso contrário.
    """
    try:
        posicoes = api.get_positions(category="linear", symbol=symbol)
        if posicoes.get("retCode") == 0:
            for p in posicoes.get("result", {}).get("list", []) or []:
                tamanho = float(p.get("size") or p.get("positionAmt") or 0)
                # Posições short na OKX têm tamanho negativo.
                if abs(tamanho) > 0:
                    return True
            return False

        # Erro técnico / API: não conclui que fechou. Trade deve permanecer vivo.
        logger.warning(
            f"🛡️ Erro técnico detectado — trade mantido por segurança (get_positions retCode={posicoes.get('retCode')})"
        )
        return True
    except Exception as e:
        logger.warning(f"🛡️ Erro técnico detectado — trade mantido por imunidade: {e}")
        return True


def pode_atualizar_sl_tp(modo, trailing_ativado):
    """
    Regra única de autoridade sobre SL/TP.
    Se o trailing estiver sob gestão da corretora, o robô NÃO pode mexer.
    """
    if modo == "CORRETORA" and trailing_ativado:
        return False
    return True



def atualizar_stop_take(api, symbol, side, novo_sl, novo_tp, trailing_offset=None):
    """
    Atualiza o SL/TP sem recalcular estratégia.
    Apenas:
    - valida entry_price
    - arredonda valores conforme tickSize
    - aplica trailing quando existir
    - evita reenvio desnecessário
    """
    try:
        # 1) Buscar posição atual diretamente da Bybit
        pos = api.get_positions(category="linear", symbol=symbol)
        if pos.get("retCode") != 0:
            logger.warning(f"⚠️ Falha ao obter posições: {pos.get('retMsg')}")
            return False
        
        lista = pos.get("result", {}).get("list", [])
        if not lista:
            logger.warning("⚠️ Nenhuma posição ativa.")
            return False
        
        atual = lista[0]

        # === ENTRY SERÁ SEMPRE O AVGPRICE DA BYBIT ===
        entry_price = float(atual.get("avgPrice", 0))
        preco_atual = float(atual.get("last") or 0)

        if entry_price <= 0:
            logger.warning("⚠️ avgPrice inválido retornado pela Bybit.")
            return False

        # stop/take atuais da posição
        sl_atual = float(atual.get("stopLoss") or 0)
        tp_atual = float(atual.get("takeProfit") or 0)
        # Somente valores devolvidos em closeOrderAlgo comprovam que a OKX
        # possui uma protecao ativa. stopLoss/takeProfit isolados podem ser
        # apenas estado legado/interno do monitor.
        close_algos = atual.get("closeOrderAlgo") or []
        protecao_confirmada_okx = isinstance(close_algos, list) and any(
            isinstance(algo, dict)
            and (algo.get("slTriggerPx") or algo.get("tpTriggerPx"))
            and str(algo.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")
            for algo in close_algos
        )



        # 3) Obter tickSize
        try:
            info = api.get_instruments_info(category="linear", symbol=symbol)
            tick_size = float(info["result"]["list"][0]["priceFilter"]["tickSize"])
        except:
            tick_size = 0.1
            logger.warning("⚠️ TickSize não encontrado, usando 0.1")

        def ajustar_tick(valor):
            casas = str(tick_size)[::-1].find('.')
            return round(round(valor / tick_size) * tick_size, casas)

        casas_tick = max(0, str(tick_size)[::-1].find('.'))

        def texto_preco(valor):
            return f"{float(valor):.{casas_tick}f}"

        # 4) Ajustar valores conforme tickSize
        novo_sl = ajustar_tick(novo_sl)

        if novo_tp is not None:
            novo_tp = ajustar_tick(novo_tp)

        # 4.1) O SL deve ficar do lado correto do preço atual, não
        # necessariamente do lado da entrada. Isso permite breakeven
        # protegido: acima da entrada em BUY e abaixo da entrada em SELL.
        referencia_stop = preco_atual if preco_atual > 0 else entry_price
        if side.lower() == "buy" and novo_sl >= referencia_stop:
            ajustado = ajustar_tick(referencia_stop - tick_size)
            if ajustado <= 0 or ajustado >= entry_price:
                ajustado = ajustar_tick(entry_price - tick_size)
            logger.warning(
                f"⚠️ SL acima do preço atual (BUY). Ajustando SL de {novo_sl:.4f} para {ajustado:.4f}."
            )
            novo_sl = ajustado
        elif side.lower() == "sell" and novo_sl <= referencia_stop:
            ajustado = ajustar_tick(referencia_stop + tick_size)
            logger.warning(
                f"⚠️ SL abaixo do preço atual (SELL). Ajustando SL de {novo_sl:.4f} para {ajustado:.4f}."
            )
            novo_sl = ajustado

        # 4.2) Proteções: TP deve respeitar o lado
        if novo_tp is not None:
            if side.lower() == "buy" and novo_tp <= entry_price:
                logger.warning("⚠️ TP inválido para BUY (<= entry). Removendo TP.")
                novo_tp = None
            elif side.lower() == "sell" and novo_tp >= entry_price:
                logger.warning("⚠️ TP inválido para SELL (>= entry). Removendo TP.")
                novo_tp = None

        # 5) Evitar reenvio se nada mudou
        tp_diff_ok = True
        if novo_tp is not None:
            tp_diff_ok = abs(tp_atual - novo_tp) < tick_size

        native_trailing_pending = bool(
            trailing_offset and trailing_offset > 0 and hasattr(api, "set_trailing_stop")
        )
        if (
            protecao_confirmada_okx
            and abs(sl_atual - novo_sl) < tick_size
            and tp_diff_ok
            and not native_trailing_pending
        ):
            logger.debug("⏩ SL/TP já estão corretos. Nenhum envio necessário.")
            return True
        if not protecao_confirmada_okx:
            logger.warning("🛡️ SL/TP não confirmado pela OKX; enviando proteção novamente.")

        # 6) Montar payload FINAL (robusto, sem TP inválido)
        payload = {
            "category": "linear",
            "symbol": symbol,
            "slTriggerBy": "LastPrice",
            "triggerDirection": 1 if side.lower() == "buy" else 2,
        }

        # SL sempre vai
        payload["stopLoss"] = texto_preco(novo_sl)

        # TP só se for válido
        if novo_tp is not None:
            payload["takeProfit"] = texto_preco(novo_tp)
            payload["tpTriggerBy"] = "LastPrice"

        # 7) Trailing nativo da OKX, separado do OCO de SL/TP.
        trailing_ok = True
        if trailing_offset and trailing_offset > 0 and hasattr(api, "set_trailing_stop"):
            try:
                trailing_response = api.set_trailing_stop(
                    category="linear",
                    symbol=symbol,
                    side="sell" if side.lower() == "buy" else "buy",
                    callbackSpread=trailing_offset,
                    replace_existing=True,
                )
                trailing_ok = trailing_response.get("retCode") == 0
                trailing_algo_id = None
                if trailing_ok:
                    trailing_rows = trailing_response.get("result", {}).get("list", []) or []
                    trailing_algo_id = trailing_rows[0].get("algoId") if trailing_rows else None
                    # retCode=0 sem algoId não prova que o trailing foi criado.
                    trailing_ok = bool(trailing_algo_id)
                if trailing_ok:
                    logger.info(
                        "✅ Trailing nativo criado na OKX | algoId=%s | callbackSpread=%.8f",
                        trailing_algo_id,
                        float(trailing_offset),
                    )
                else:
                    logger.error(
                        "❌ OKX não confirmou trailing nativo (sem algoId): %s",
                        trailing_response,
                    )
            except Exception as exc:
                trailing_ok = False
                logger.error("❌ Falha ao criar trailing nativo na OKX: %s", exc)
        elif trailing_offset and trailing_offset > 0:
            # Compatibilidade com adaptadores que implementam trailing no
            # próprio endpoint de atualização de SL/TP.
            payload["trailingStop"] = f"{trailing_offset:.2f}"

        # 8) Enviar atualização segura de SL/TP
        # Se já existe um OCO confirmado e estamos alterando SL/TP/trailing,
        # a camada OKX precisa substituir o algo anterior antes de criar o novo.
        response = api.set_trading_stop(
            **payload,
            replace_existing=bool(protecao_confirmada_okx),
        )

        if response.get("retCode") == 0 and trailing_ok:
            logger.info(f"✅ SL/TP atualizados: {payload}")
            return True

        logger.error(
            "❌ Proteção incompleta | SL/TP=%s | trailing_ok=%s",
            response.get("retMsg"),
            trailing_ok,
        )
        return False

    except Exception as e:
        logger.error(f"❌ Erro crítico no atualizar_stop_take: {e}", exc_info=True)
        return False




def monitorar_ordem_ate_encerrar(trade_id, symbol, position_size, entry_price, side, api, risk_parameters, indicators):
    global trade_state

    # ==========================
    # Validações iniciais
    # ==========================
    trade_id_raw = str(trade_id or "").strip().lower()
    if not trade_id_raw or trade_id_raw in ("0", "from_bybit", "none", "null"):
        recovered = OrdemUtils.get_last_filled_order_id(api, symbol)
        if recovered:
            trade_id = recovered
            logger.warning(f"trade_id invalido; recuperado do historico: {trade_id}")
        else:
            trade_id = str(uuid.uuid4())
            logger.warning(f"trade_id invalido; gerado uuid: {trade_id}")

    if not entry_price or entry_price == 0:
        logger.warning("entry_price ausente. Abortando monitoramento.")
        return

    strategy = None
    if isinstance(indicators, dict):
        strategy = indicators.get("strategy") or indicators.get("estrategia")
    if not strategy and isinstance(risk_parameters, dict):
        strategy = risk_parameters.get("strategy") or risk_parameters.get("estrategia_utilizada")
    if not strategy:
        strategy = "bybit_existing"

    atr_valor_monitor, allow_trailing_flag, trailing_be_liberado = validar_atr_para_trailing(
        indicators, symbol, strategy
    )

    # ==================================================
    # 🧠 INICIALIZAÇÃO DO ESTADO DO TRADE (OBRIGATÓRIA)
    # ==================================================
    side_normalized = str(side or "").strip().lower()
    if side_normalized not in ("buy", "sell"):
        raise ValueError(f"lado da posição inválido para monitoramento: {side!r}")

    if "entry_ts" not in trade_state:
        trade_state.update({
            "entry_ts": time.time(),                       # ⏱️ nascimento do trade
            "entry_price": entry_price,                    # 💰 preço de entrada real
            "side": side_normalized,                       # buy / sell
            "atr_entry": float(atr_valor_monitor or 0.0), # 📐 ATR congelado real (ou 0 no fail-safe)
            "breakout_level": entry_price,                        # se não tiver, ok
            "strategy": strategy,
            "modo": (
                str(strategy).strip().lower()
                if strategy and str(strategy).strip().lower() not in ("unknown", "")
                else "breakout"
            ),
            "imunidade_breakout": True,                    # janela de imunidade ativa
            "candle_count": 0                              # imunidade por candle
        })
        logger.info("🧠 trade_state inicializado para transition manager")
    else:
        # Uma recuperação posterior deve corrigir estado legado/incompleto.
        trade_state["side"] = side_normalized
        trade_state["entry_price"] = entry_price

    # ==========================
    # Início do monitoramento
    # ==========================
    logger.info(f"🚨 Monitoramento iniciado para trade_id: {trade_id}")

    try:
        side_str = (side or "unknown").upper()
        qty_str = f"{float(position_size):.6f}" if position_size is not None else "N/A"
        entry_str = f"{float(entry_price):.6f}" if entry_price is not None else "N/A"
        strategy_str = (strategy or "unknown").upper()
        panel_url = os.getenv("GENESIS_PANEL_URL", "https://genesis-panel.onrender.com").rstrip("/")
        msg = (
            f"🔔 ORDEM ABERTA | estratégia={strategy_str} | ativo={symbol} | direção={side_str} | "
            f"entrada={entry_str} | quantidade={qty_str} | trade_id={trade_id}\n"
            f"Ver posição ao vivo: {panel_url}/positions"
        )
        send_telegram_message(msg)
    except Exception:
        pass

    # Preenche SL/TP se ausentes antes de salvar no banco
    try:
        if isinstance(risk_parameters, dict):
            if risk_parameters.get("stop_loss") is None or risk_parameters.get("take_profit") is None:
                pos = api.get_positions(category="linear", symbol=symbol)
                if pos.get("retCode") == 0:
                    lista = pos.get("result", {}).get("list", []) or []
                    if lista:
                        pos_sl = lista[0].get("stopLoss")
                        pos_tp = lista[0].get("takeProfit")
                        allow_tp_none = _strategy_allows_tp_none(strategy)
                        if risk_parameters.get("stop_loss") is None and pos_sl not in (None, "", "0", "0.0", 0):
                            risk_parameters["stop_loss"] = float(pos_sl)
                        if (
                            risk_parameters.get("take_profit") is None
                            and pos_tp not in (None, "", "0", "0.0", 0)
                            and not allow_tp_none
                        ):
                            risk_parameters["take_profit"] = float(pos_tp)
            if risk_parameters.get("strategy") is None:
                risk_parameters["strategy"] = strategy
    except Exception as e:
        logger.warning(f"Falha ao preencher SL/TP/strategy antes de salvar trade: {e}")

    if str(strategy or "").strip().upper() == "SWING_TREND_FOLLOWING":
        try:
            params = risk_parameters if isinstance(risk_parameters, dict) else {}
            notify_swing_entry(
                symbol=symbol,
                side=side_normalized,
                entry=float(entry_price),
                stop=float(params.get("stop_loss") or 0.0),
                target=float(params.get("take_profit") or 0.0),
                trailing=float(params.get("trailing_stop") or params.get("trailing_distance") or 0.0),
                quantity=abs(float(position_size or 0.0)),
                order_id=str(trade_id),
            )
        except Exception as exc:
            logger.warning("[TELEGRAM] alerta grafico de entrada ignorado: %s", type(exc).__name__)

    _insert_trade_if_missing(
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        strategy=strategy,
        risk_parameters=risk_parameters if isinstance(risk_parameters, dict) else None,
        indicators=indicators if isinstance(indicators, dict) else None,
    )

    operacao_em_andamento = True
    TEMPO_MAX_MONITORAMENTO = int(os.getenv("TEMPO_MAX_MONITORAMENTO", 1800))
    tempo_inicio = time.time()

    # Flags
    breakeven_ja_ativado = False
    trailing_ja_ativado = False
    # Estado interno não prova que a OKX criou o trailing. A confirmação real
    # exige o algoId devolvido pelo endpoint nativo de trailing.
    trailing_nativo_confirmado = False
    parcial_ja_feita = False
    parcial_ativada = False
    parcial_bloqueada_por_taxa = False

    trailing_offset = 0.0
    ciclo_monitoramento = 0
    atr_fail_safe_logged = False

    # 🔒 Confirmação dupla de fechamento (anti-lag Bybit)
    sem_posicao_count = 0
    CONFIRMACOES_FECHAMENTO = 2

    INTERVALO_MONITORAMENTO = int(os.getenv("INTERVALO_MONITORAMENTO", 5))
    ultimo_ciclo = 0

    # ✅ Declara fora do while
    ultimo_candle_timestamp = None
    df = None

    # ==========================
    # Configs: parcial / custos (fee-aware)
    # ==========================
    PARCIAL_PROGRESS_PCT = float(os.getenv("PARCIAL_PROGRESS_PCT", 0.55))
    PARCIAL_FRACTION = float(os.getenv("PARCIAL_FRACTION", 0.30))

    PARCIAL_FEE_AWARE = os.getenv("PARCIAL_FEE_AWARE", "1") in ("1", "true", "True", "yes", "YES")
    PARCIAL_FEE_MULTIPLIER = float(os.getenv("PARCIAL_FEE_MULTIPLIER", 3.0))

    # Env em "percent" (ex.: 0.018 = 0,018%)
    FUTURES_MAKER_FEE_RATE = float(os.getenv("FUTURES_MAKER_FEE_PCT", 0.018)) / 100.0
    FUTURES_TAKER_FEE_RATE = float(os.getenv("FUTURES_TAKER_FEE_PCT", 0.0495)) / 100.0
    PARCIAL_ASSUME_ENTRY_FEE_RATE = float(os.getenv("PARCIAL_ASSUME_ENTRY_FEE_PCT", 0.018)) / 100.0

    PARCIAL_MAKER_FIRST = os.getenv("PARCIAL_MAKER_FIRST", "1") in ("1", "true", "True", "yes", "YES")
    PARCIAL_MAKER_TIMEOUT_SEC = float(os.getenv("PARCIAL_MAKER_TIMEOUT_SEC", 8))
    PARCIAL_MAKER_OFFSET_PCT = float(os.getenv("PARCIAL_MAKER_OFFSET_PCT", 0.0002))

    # ==========================
    # Configs: decisão/ extensão perto do TP
    # ==========================
    TP_DECISION_ZONE_PCT = float(os.getenv("TP_DECISION_ZONE_PCT", 0.95))
    TP_FORCE_EXIT_ZONE_PCT = float(os.getenv("TP_FORCE_EXIT_ZONE_PCT", 0.95))
    TP_EXTENSION_ENABLE = os.getenv("TP_EXTENSION_ENABLE", "1") in ("1", "true", "True", "yes", "YES")
    TP_EXTENSION_STEP_PCT = float(os.getenv("TP_EXTENSION_STEP_PCT", 0.12))
    TP_EXTENSION_ABS_MIN = float(os.getenv("TP_EXTENSION_ABS_MIN", 0.0))
    TP_EXTENSION_MAX_STEPS = int(os.getenv("TP_EXTENSION_MAX_STEPS", 4))
    TP_EXTENSION_SCORE_MIN = int(os.getenv("TP_EXTENSION_SCORE_MIN", 2))
    TP_DECISION_COOLDOWN_SEC = int(os.getenv("TP_DECISION_COOLDOWN_SEC", 45))
    TP_EXTENSION_TRAILING_TIGHTEN = float(os.getenv("TP_EXTENSION_TRAILING_TIGHTEN", 0.90))
    TP_EXTENSION_TRAILING_MIN = float(os.getenv("TP_EXTENSION_TRAILING_MIN", 0.0))

    while True:

        # ======================================================
        # 🔧 WATCHDOG DE POSIÇÃO — CONFIRMAÇÃO DUPLA
        # ======================================================
        if not ordem_ainda_aberta(api, symbol):
            sem_posicao_count += 1
            logger.warning(
                f"⚠️ Posição ausente detectada ({sem_posicao_count}/{CONFIRMACOES_FECHAMENTO}) "
                f"para {symbol}"
            )

            if sem_posicao_count >= CONFIRMACOES_FECHAMENTO:
                logger.warning(
                    f"🚩 Fechamento CONFIRMADO para {symbol}. "
                    f"Encerrando monitoramento definitivamente."
                )
                exit_price = None
                profit_loss = None
                try:
                    resp = api.get_closed_pnl(category="linear", symbol=symbol, limit=1)
                    if (
                        resp.get("retCode") == 0
                        and resp.get("result", {}).get("list")
                    ):
                        item = resp["result"]["list"][0]
                        exit_price = float(item.get("exitPrice") or item.get("avgExitPrice") or 0) or None
                        if item.get("closedPnl") is not None:
                            profit_loss = float(item.get("closedPnl") or 0)

                except Exception as e:
                    logger.warning(f"Falha ao obter PnL fechado: {e}")

                if exit_price is None:
                    try:
                        ticker = api.get_tickers(category="linear", symbol=symbol)
                        exit_price = float(ticker["result"]["list"][0]["lastPrice"])
                    except Exception as e:
                        logger.warning(f"Falha ao obter ultimo preco: {e}")

                if exit_price is not None and profit_loss is None:
                    try:
                        qty = float(position_size)
                        if side.lower() == "buy":
                            profit_loss = (exit_price - float(entry_price)) * qty
                        else:
                            profit_loss = (float(entry_price) - exit_price) * qty
                    except Exception:
                        profit_loss = None

                _insert_trade_if_missing(
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    profit_loss=profit_loss,
                    strategy=strategy,
                    risk_parameters=risk_parameters if isinstance(risk_parameters, dict) else None,
                    indicators=indicators if isinstance(indicators, dict) else None,
                    regime=regime_mercado if "regime_mercado" in locals() else None,
                    tendencia=tendencia_mercado if "tendencia_mercado" in locals() else None,
                )
                try:
                    close_ts = datetime.utcnow().isoformat()
                    atualizar_trade_sqlite(
                        trade_id=trade_id,
                        exit_price=exit_price,
                        profit_loss=profit_loss,
                        symbol=symbol,
                        entry_price=entry_price,
                        close_ts=close_ts,
                        trailing_stop_final=(risk_parameters or {}).get("trailing_stop"),
                        status="fechado",
                    )
                except Exception as e:
                    logger.warning(f"Falha ao atualizar trade no banco: {e}")
                    close_ts = datetime.utcnow().isoformat()
                try:
                    entry_ts_raw = trade_state.get("entry_ts")
                    duracao = None
                    if entry_ts_raw not in (None, ""):
                        try:
                            duracao = max(0.0, float(time.time()) - float(entry_ts_raw))
                        except Exception:
                            duracao = None

                    score_trade = None
                    if isinstance(risk_parameters, dict):
                        score_trade = risk_parameters.get("entry_score") or risk_parameters.get("score")
                    if score_trade in (None, "") and isinstance(indicators, dict):
                        score_trade = indicators.get("entry_score") or indicators.get("score")

                    regime_trade = None
                    if isinstance(risk_parameters, dict):
                        regime_trade = risk_parameters.get("regime")
                    if regime_trade in (None, "") and "regime_mercado" in locals():
                        regime_trade = regime_mercado

                    motivo_saida = "manual"
                    stop_ref = (risk_parameters or {}).get("stop_loss") if isinstance(risk_parameters, dict) else None
                    take_ref = (risk_parameters or {}).get("take_profit") if isinstance(risk_parameters, dict) else None
                    if exit_price is not None and stop_ref not in (None, "", 0, "0", "0.0"):
                        if abs(float(exit_price) - float(stop_ref)) <= max(0.01, abs(float(stop_ref)) * 0.002):
                            motivo_saida = "stop"
                    if (
                        exit_price is not None
                        and take_ref not in (None, "", 0, "0", "0.0")
                        and abs(float(exit_price) - float(take_ref)) <= max(0.01, abs(float(take_ref)) * 0.002)
                    ):
                        motivo_saida = "take"

                    registrar_trade_fechado(
                        {
                            "symbol": symbol,
                            "side": (side or "").lower() or None,
                            "entrada": float(entry_price) if entry_price is not None else None,
                            "saida": float(exit_price) if exit_price is not None else None,
                            "resultado": "win" if (profit_loss or 0.0) > 0 else "loss",
                            "pnl": float(profit_loss) if profit_loss is not None else None,
                            "regime": regime_trade,
                            "score": float(score_trade) if score_trade not in (None, "") else None,
                            "estrategia": strategy,
                            "timestamp_entrada": entry_ts_raw,
                            "timestamp_saida": close_ts,
                            "duracao": duracao,
                            "motivo_saida": motivo_saida,
                        }
                    )
                    try:
                        rsi_trade = None
                        atr_trade = None
                        volume_trade = None
                        volume_ratio_trade = None
                        lstm_signal_trade = None
                        sentimento_trade = None
                        tendencia_trade = None
                        volatilidade_trade = None
                        distancia_resistencia_trade = None
                        distancia_suporte_trade = None
                        inclinacao_ema50_trade = None
                        inclinacao_ema200_trade = None
                        atr_normalizado_trade = None
                        posicao_bollinger_trade = None
                        candles_desde_breakout_trade = None
                        confluencia_sinais_trade = None

                        if isinstance(indicators, dict):
                            rsi_trade = indicators.get("RSI") or indicators.get("rsi")
                            atr_trade = indicators.get("ATR") or indicators.get("atr") or indicators.get("atr_entrada")
                            volume_trade = indicators.get("volume") or indicators.get("VOLUME")
                            lstm_signal_trade = indicators.get("decisao_lstm") or indicators.get("lstm_signal")
                            sentimento_trade = (
                                indicators.get("sentimento")
                                or indicators.get("sentimento_entrada")
                                or indicators.get("sentiment_score")
                                )
                            candles_desde_breakout_trade = indicators.get("candles_desde_breakout")

                        if isinstance(risk_parameters, dict):
                            if rsi_trade in (None, ""):
                                rsi_trade = risk_parameters.get("RSI") or risk_parameters.get("rsi")
                            if atr_trade in (None, ""):
                                atr_trade = risk_parameters.get("ATR") or risk_parameters.get("atr") or risk_parameters.get("atr_entrada")
                            if volume_trade in (None, ""):
                                volume_trade = risk_parameters.get("volume")
                            if lstm_signal_trade in (None, ""):
                                lstm_signal_trade = risk_parameters.get("decisao_lstm") or risk_parameters.get("lstm_signal")
                            if sentimento_trade in (None, ""):
                                sentimento_trade = (
                                    risk_parameters.get("sentimento")
                                    or risk_parameters.get("sentimento_entrada")
                                    or risk_parameters.get("sentiment_score")
                                )
                            tendencia_trade = (
                                risk_parameters.get("tendencia")
                                or risk_parameters.get("tendencia_macro")
                                or risk_parameters.get("regime")
                            )
                            if candles_desde_breakout_trade in (None, ""):
                                candles_desde_breakout_trade = risk_parameters.get("candles_desde_breakout")

                        if volume_trade not in (None, "") and "df" in locals() and df is not None and not getattr(df, "empty", True):
                            try:
                                media_volume = float(df["volume"].tail(20).mean())
                                if media_volume > 0:
                                    volume_ratio_trade = float(float(volume_trade) / media_volume)
                            except Exception:
                                volume_ratio_trade = None

                            try:
                                close_trade = float(df["close"].iloc[-1])
                            except Exception:
                                close_trade = float(entry_price) if entry_price not in (None, "", 0, "0", "0.0") else None

                            try:
                                resistencia_trade = float(df["high"].tail(20).max())
                                if close_trade and close_trade > 0:
                                    distancia_resistencia_trade = float((resistencia_trade - close_trade) / close_trade)
                            except Exception:
                                distancia_resistencia_trade = None

                            try:
                                suporte_trade = float(df["low"].tail(20).min())
                                if close_trade and close_trade > 0:
                                    distancia_suporte_trade = float((close_trade - suporte_trade) / close_trade)
                            except Exception:
                                distancia_suporte_trade = None

                            try:
                                if "EMA50" in df.columns and len(df) >= 2:
                                    inclinacao_ema50_trade = float(df["EMA50"].iloc[-1] - df["EMA50"].iloc[-2])
                            except Exception:
                                inclinacao_ema50_trade = None

                            try:
                                if "EMA200" in df.columns and len(df) >= 2:
                                    inclinacao_ema200_trade = float(df["EMA200"].iloc[-1] - df["EMA200"].iloc[-2])
                            except Exception:
                                inclinacao_ema200_trade = None

                            try:
                                if (
                                    close_trade
                                    and close_trade > 0
                                    and atr_trade not in (None, "", 0, "0", "0.0")
                                ):
                                    atr_normalizado_trade = float(float(atr_trade) / close_trade)
                            except Exception:
                                atr_normalizado_trade = None

                            try:
                                boll_upper = None
                                boll_lower = None
                                for upper_col in ("BB_upper", "BOLL_UPPER", "boll_upper"):
                                    if upper_col in df.columns:
                                        boll_upper = float(df[upper_col].iloc[-1])
                                        break
                                for lower_col in ("BB_lower", "BOLL_LOWER", "boll_lower"):
                                    if lower_col in df.columns:
                                        boll_lower = float(df[lower_col].iloc[-1])
                                        break
                                if (
                                    boll_upper is not None
                                    and boll_lower is not None
                                    and close_trade is not None
                                    and (boll_upper - boll_lower) != 0
                                ):
                                    posicao_bollinger_trade = float((close_trade - boll_lower) / (boll_upper - boll_lower))
                            except Exception:
                                posicao_bollinger_trade = None

                        if atr_trade not in (None, "") and entry_price not in (None, "", 0, "0", "0.0"):
                            try:
                                volatilidade_trade = float(float(atr_trade) / float(entry_price))
                            except Exception:
                                volatilidade_trade = None

                        try:
                            lstm_norm = 0
                            sent_norm = 0
                            raw_lstm = str(lstm_signal_trade or "").strip().lower()
                            raw_sent = str(sentimento_trade or "").strip().lower()
                            if raw_lstm in ("buy", "up", "alta", "positivo", "bullish"):
                                lstm_norm = 1
                            elif raw_lstm in ("sell", "down", "baixa", "negativo", "bearish"):
                                lstm_norm = -1
                            if raw_sent in ("positivo", "bullish", "buy", "alta"):
                                sent_norm = 1
                            elif raw_sent in ("negativo", "bearish", "sell", "baixa"):
                                sent_norm = -1
                            confluencia_sinais_trade = int(lstm_norm == sent_norm) if lstm_norm != 0 and sent_norm != 0 else 0
                        except Exception:
                            confluencia_sinais_trade = None

                        registrar_trade_quality_fechado(
                            {
                                "timestamp_saida": close_ts,
                                "rsi": rsi_trade,
                                "atr": atr_trade,
                                "volume_ratio": volume_ratio_trade,
                                "lstm_signal": lstm_signal_trade,
                                "sentimento": sentimento_trade,
                                "estrategia": strategy,
                                "tendencia": tendencia_trade or regime_trade,
                                "volatilidade": volatilidade_trade,
                                "score_entrada": score_trade,
                                "distancia_resistencia": distancia_resistencia_trade,
                                "distancia_suporte": distancia_suporte_trade,
                                "inclinacao_ema50": inclinacao_ema50_trade,
                                "inclinacao_ema200": inclinacao_ema200_trade,
                                "atr_normalizado": atr_normalizado_trade,
                                "posicao_bollinger": posicao_bollinger_trade,
                                "candles_desde_breakout": candles_desde_breakout_trade,
                                "confluencia_sinais": confluencia_sinais_trade,
                                "resultado": "win" if (profit_loss or 0.0) > 0 else "loss",
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Falha ao registrar trade quality fechado: {e}")
                except Exception as e:
                    logger.warning(f"Falha ao registrar feedback de trade fechado: {e}")
                try:
                    side_str = (side or "unknown").upper()
                    entry_str = f"{float(entry_price):.6f}" if entry_price is not None else "N/A"
                    exit_str = f"{float(exit_price):.6f}" if exit_price is not None else "N/A"
                    pnl_str = f"{float(profit_loss):.6f}" if profit_loss is not None else "N/A"
                    strategy_str = (strategy or "unknown").upper()
                    panel_url = os.getenv("GENESIS_PANEL_URL", "https://genesis-panel.onrender.com").rstrip("/")
                    msg = (
                        f"✅ ORDEM FECHADA | estratégia={strategy_str} | ativo={symbol} | direção={side_str}\n"
                        f"entrada={entry_str} | saída={exit_str} | resultado={pnl_str} | motivo={motivo_saida}\n"
                        f"Ver detalhe: {panel_url}/trades/{trade_id}"
                    )
                    send_telegram_message(msg)
                    if str(strategy or "").strip().upper() == "SWING_TREND_FOLLOWING":
                        notify_swing_exit(
                            symbol=symbol,
                            side=side,
                            entry=float(entry_price),
                            exit_price=float(exit_price or 0.0),
                            quantity=abs(float(position_size or 0.0)),
                            reason=motivo_saida,
                            order_id=str(trade_id),
                        )
                except Exception:
                    pass
                break
        else:
            # 🔄 Posição ainda existe → zera contador
            if sem_posicao_count > 0:
                logger.info("🔄 Posição voltou a existir. Resetando contador de confirmação.")
            sem_posicao_count = 0


        agora = time.time()
        if agora - ultimo_ciclo < INTERVALO_MONITORAMENTO:
            time.sleep(0.5)
            continue
        ultimo_ciclo = agora
        ciclo_monitoramento += 1
        tempo_decorrido = time.time() - tempo_inicio

        try:
            # --- Validação defensiva ---
            if indicators is None or indicators is ... or not isinstance(indicators, dict):
                logger.error(f"⚠️ indicators inválido: {indicators}")
                indicators = {}
            if risk_parameters is None or risk_parameters is ... or not isinstance(risk_parameters, dict):
                logger.error(f"⚠️ risk_parameters inválido: {risk_parameters}")
                risk_parameters = {}

            # --- Obter dados de mercado ---
            df_raw = get_market_data_bybit(symbol, api, interval="5", limit=200)

            if df_raw is None or df_raw.empty:
                time.sleep(15)
                continue

            ultimo_candle = df_raw["time"].iloc[-1]

            # 🔄 Só recalcula indicadores se o candle mudou
            if ultimo_candle != ultimo_candle_timestamp:
                df = calculate_indicators(df_raw).bfill().ffill()
                ultimo_candle_timestamp = ultimo_candle

                # Em recuperações de posição, o dicionário de entrada pode vir
                # com ATR=0 mesmo quando os candles possuem volatilidade válida.
                # Reidrata o ATR a partir do dataframe para não bloquear o
                # trailing/breakeven indefinidamente.
                if atr_valor_monitor is None or atr_valor_monitor <= 0:
                    atr_candle = None
                    try:
                        atr_candle = float(df["ATR"].iloc[-1])
                    except Exception:
                        atr_candle = _atr_fallback_dos_candles(df)
                    if atr_candle is None or not math.isfinite(atr_candle) or atr_candle <= 0:
                        atr_candle = _atr_fallback_dos_candles(df)
                    if atr_candle is not None and atr_candle > 0 and allow_trailing_flag:
                        atr_valor_monitor = atr_candle
                        trailing_be_liberado = True
                        trade_state["atr_entry"] = atr_candle
                        logger.warning(
                            f"✅ ATR reidratado dos candles para trailing: "
                            f"{atr_candle:.8f} | symbol={symbol}"
                        )

                # 🔥 PREÇO REAL-TIME DA BYBIT
                try:
                    ticker = api.get_tickers(category="linear", symbol=symbol)
                    last_price = float(ticker["result"]["list"][0]["lastPrice"])
                    logger.info(f"🟢 Preço REAL-TIME detectado: {last_price:.2f}")
                except Exception as e:
                    last_price = float(df["close"].iloc[-1])
                    logger.warning(f"⚠️ Falha ao obter preço real-time. Usando close do candle ({last_price:.2f}). Erro: {e}")

                logger.info(f"🕐 Novo candle detectado → indicadores recalculados ({ultimo_candle})")

                # 🛡️ CONTROLE DA IMUNIDADE BREAKOUT BASEADO EM ATR
                atr_entry = trade_state.get("atr_entry", 0.0)
                side = trade_state.get("side")
                if str(side or "").lower() not in ("buy", "sell"):
                    logger.error("❌ Side ausente/inválido no trade_state durante monitoramento. Ciclo ignorado.")
                    time.sleep(1)
                    continue
                last_close = float(df["close"].iloc[-1])

                # Imunidade temporal por N candles (independente de ruído intrabar)
                if trade_state.get("modo") == "breakout":
                    trade_state["candle_count"] = trade_state.get("candle_count", 0) + 1
                    trade_state["imunidade_breakout"] = trade_state["candle_count"] <= BREAKOUT_IMUNIDADE_CANDLES
                    if trade_state["imunidade_breakout"]:
                        logger.info(
                            f"🛡️ Imunidade por candle ativa "
                            f"({trade_state['candle_count']}/{BREAKOUT_IMUNIDADE_CANDLES})"
                        )

                if side.lower() == "buy":
                    limite_imunidade = entry_price - (BREAKOUT_IMUNIDADE_ATR_FRAC * atr_entry)
                    condicao_saida = last_price < limite_imunidade
                    motivo = f"preço {last_price:.2f} < {limite_imunidade:.2f}"
                else:  # sell
                    limite_imunidade = entry_price + (BREAKOUT_IMUNIDADE_ATR_FRAC * atr_entry)
                    condicao_saida = last_price > limite_imunidade
                    motivo = f"preço {last_price:.2f} > {limite_imunidade:.2f}"
                # ATR apenas para telemetria; não força saída durante imunidade temporal
                if condicao_saida:
                    logger.debug(f"📏 ATR check: {motivo}")

            else:
                # 🔥 PREÇO REAL-TIME DURANTE O CANDLE ATUAL
                try:
                    ticker = api.get_tickers(category="linear", symbol=symbol)
                    last_price = float(ticker["result"]["list"][0]["lastPrice"])
                    logger.debug(f"🟢 Preço REAL-TIME no candle atual: {last_price:.2f}")
                except Exception as e:
                    last_price = float(df_raw["close"].iloc[-1])
                    logger.warning(f"⚠️ Falha no real-time. Usando preço do candle ({last_price:.2f}). Erro: {e}")

            trade_state["last_price"] = last_price

            # --- SL/TP adaptativo (usa df mais recente) ---
            stop_loss_calc, take_profit_calc, _, _ = calcular_stop_take_adaptativo(
                df, entry_price, side, tipo_stop=risk_parameters.get("tipo_stop")
            )

            # ============================================================
            # 1️⃣ CONGELAMENTO DEFINITIVO DO SL/TP INICIAIS (UMA ÚNICA VEZ)
            # ============================================================
            if not trade_state["congelado"]:

                sl = risk_parameters.get("stop_loss")
                tp = risk_parameters.get("take_profit")

                if sl is None or tp is None:
                    try:
                        pos = api.get_positions(category="linear", symbol=symbol)
                        if pos.get("retCode") == 0:
                            lista = pos.get("result", {}).get("list", []) or []
                            if lista:
                                pos_sl_raw = lista[0].get("stopLoss")
                                pos_tp_raw = lista[0].get("takeProfit")
                                pos_sl = float(pos_sl_raw) if pos_sl_raw not in (None, "", "0", "0.0", 0) else None
                                pos_tp = float(pos_tp_raw) if pos_tp_raw not in (None, "", "0", "0.0", 0) else None
                                if sl is None and pos_sl is not None:
                                    sl = pos_sl
                                if tp is None and pos_tp is not None:
                                    tp = pos_tp
                    except Exception as e:
                        logger.warning(f"Falha ao ler SL/TP da corretora: {e}")

                if sl is None:
                    sl = stop_loss_calc
                if tp is None:
                    tp = take_profit_calc

                allow_tp_none = _strategy_allows_tp_none(strategy)
                if allow_tp_none:
                    tp = None

                # --- Correção antes de congelar ---
                if side.lower() == "buy":
                    if sl >= entry_price:
                        logger.warning(f"⚠️ SL inicial inválido para BUY ({sl:.2f}). Ajustando para 1% abaixo da entrada.")
                        sl = entry_price * 0.99
                    if tp is not None and tp <= entry_price:
                        logger.warning(f"⚠️ TP inicial inválido para BUY ({tp:.2f}). Ajustando para 1% acima da entrada.")
                        tp = entry_price * 1.01

                else:  # SELL
                    if sl <= entry_price:
                        logger.warning(f"⚠️ SL inicial inválido para SELL ({sl:.2f}). Ajustando para 1% acima da entrada.")
                        sl = entry_price * 1.01
                    if tp is not None and tp >= entry_price:
                        logger.warning(f"⚠️ TP inicial inválido para SELL ({tp:.2f}). Ajustando para 1% abaixo da entrada.")
                        tp = entry_price * 0.99

                trade_state["sl_inicial"] = sl
                trade_state["tp_inicial"] = tp
                trade_state["congelado"] = True
                if tp is None:
                    tp = 0.0

                logger.info(f"🧱 SL/TP INICIAIS CONGELADOS DEFINITIVAMENTE: SL={sl:.2f} | TP={tp:.2f}")

                tp = trade_state["tp_inicial"]
                # ============================================================
                # 🧱 ANCORAGEM ESTRUTURAL (NO MOMENTO DA ENTRADA)
                # Regra soberana: Stop só anda quando a estrutura anda.
                # ============================================================
                try:
                    lookback_estrutura = int(os.getenv("ESTRUTURA_LOOKBACK", "20"))
                except Exception:
                    lookback_estrutura = 20

                try:
                    janela_estrutura = df.tail(lookback_estrutura + 1).iloc[:-1]
                    if side.lower() == "buy":
                        nivel_estrutura = float(janela_estrutura["high"].max()) if not janela_estrutura.empty else None
                        tipo_estrutura = "topo"
                    else:
                        nivel_estrutura = float(janela_estrutura["low"].min()) if not janela_estrutura.empty else None
                        tipo_estrutura = "fundo"

                    trade_state["estrutura_nivel"] = nivel_estrutura
                    trade_state["estrutura_tipo"] = tipo_estrutura
                    trade_state["estrutura_lookback"] = lookback_estrutura

                    if nivel_estrutura is None:
                        trade_state["estrutura_confirmada"] = True
                        logger.warning(
                            "⚠️ Estrutura não pôde ser ancorada (janela vazia) — trailing/ajustes não serão bloqueados por estrutura."
                        )
                    else:
                        trade_state["estrutura_confirmada"] = False
                        logger.info(
                            f"🧱 Estrutura ancorada | tipo={tipo_estrutura} | nivel={nivel_estrutura:.2f} | lookback={lookback_estrutura}"
                        )
                except Exception as e:
                    trade_state["estrutura_confirmada"] = True
                    trade_state["estrutura_nivel"] = None
                    trade_state["estrutura_tipo"] = None
                    trade_state["estrutura_lookback"] = None
                    logger.warning(
                        f"⚠️ Falha ao ancorar estrutura — trailing/ajustes não serão bloqueados por estrutura: {e}"
                    )

            # ============================================================
            # 2️⃣ DEPOIS DO CONGELAMENTO, SEMPRE utilizar SOMENTE eles
            # ============================================================
            stop_loss = trade_state["sl_inicial"]
            take_profit = trade_state["tp_inicial"]
            try:
                pos_sync = api.get_positions(category="linear", symbol=symbol)
                if pos_sync.get("retCode") == 0:
                    lista_sync = pos_sync.get("result", {}).get("list", []) or []
                    if lista_sync:
                        sl_sync_raw = lista_sync[0].get("stopLoss")
                        if sl_sync_raw not in (None, "", "0", "0.0", 0):
                            sl_sync = float(sl_sync_raw)
                            if stop_loss is None or abs(sl_sync - float(stop_loss)) > 1e-9:
                                logger.info(f"🔁 SL sincronizado com corretora: {stop_loss} -> {sl_sync}")
                                stop_loss = sl_sync
                                trade_state["sl_inicial"] = sl_sync
                                risk_parameters["stop_loss"] = sl_sync
            except Exception as e:
                logger.debug(f"Falha ao sincronizar SL com corretora: {e}")

            strategy_norm = str(strategy).strip().lower()
            if strategy_norm == "early_trend":
                partial_r = float(os.getenv("EARLY_TREND_PARTIAL_R", 1.0))
                partial_fraction = float(os.getenv("EARLY_TREND_PARTIAL_FRACTION", 0.30))
                if take_profit is not None and stop_loss is not None:
                    risk_dist = abs(float(entry_price) - float(stop_loss))
                    target_dist = abs(float(take_profit) - float(entry_price))
                    if risk_dist > 0 and target_dist > 0:
                        rrr = target_dist / risk_dist
                        if rrr > 0:
                            PARCIAL_PROGRESS_PCT = min(1.0, float(partial_r) / rrr)
                PARCIAL_FRACTION = float(partial_fraction)

            old_sl = risk_parameters.get("stop_loss")
            old_tp = risk_parameters.get("take_profit")

            # 🛡️ Blindagem total contra None
            sl_ok = (
                stop_loss is not None
                and old_sl is not None
                and entry_price
                and abs(stop_loss - old_sl) / entry_price < 0.001
            )

            tp_ok = (
                take_profit is not None
                and old_tp is not None
                and entry_price
                and abs(take_profit - old_tp) / entry_price < 0.001
            )

            if sl_ok and (tp_ok or take_profit is None):
                logger.debug("⏩ Pequena variação de SL/TP detectada. Nenhum reenvio necessário.")
            else:
                risk_parameters["stop_loss"] = stop_loss
                risk_parameters["take_profit"] = take_profit

            # 🧠 Log seguro (log nunca quebra robô)
            tp_log = f"{take_profit:.2f}" if take_profit is not None else "None"

            logger.info(
                f"🔄 Ciclo #{ciclo_monitoramento} | Tempo={tempo_decorrido:.1f}s | "
                f"Preço={last_price:.2f} | Entry={entry_price:.2f} | "
                f"SL={stop_loss:.2f} | TP={tp_log}"
            )


            # === CÁLCULO DO PROGRESSO ===
            # =====================================================
            # 📈 CÁLCULO DE PROGRESSO (TP OU MOVIMENTO)
            # =====================================================
            # TP pode estar "contaminado" (ex.: BUY com TP <= entry, ou SELL com TP >= entry).
            # Nesses casos, buscamos o takeProfit real da posição na Bybit e usamos apenas para calcular o progresso.
            tp_para_progresso = take_profit
            if tp_para_progresso is not None:
                try:
                    tp_local = float(tp_para_progresso)
                    entry_f = float(entry_price)
                    tp_invalido = (side.lower() == "buy" and tp_local <= entry_f) or (
                        side.lower() == "sell" and tp_local >= entry_f
                    )
                except Exception:
                    tp_invalido = True

                if tp_invalido:
                    try:
                        pos = api.get_positions(category="linear", symbol=symbol)
                        tp_real = 0.0
                        if pos.get("retCode") == 0:
                            lista = pos.get("result", {}).get("list", []) or []
                            if lista:
                                tp_real_str = lista[0].get("takeProfit")
                                if tp_real_str not in (None, "", "0", "0.0", 0):
                                    tp_real = float(tp_real_str)

                        if tp_real > 0 and (
                            (side.lower() == "buy" and tp_real > entry_f)
                            or (side.lower() == "sell" and tp_real < entry_f)
                        ):
                            logger.warning(
                                f"⚠️ TP local inválido para cálculo do progresso (tp_local={take_profit}, entry={entry_price}). "
                                f"Usando TP real da Bybit (tp_real={tp_real})."
                            )
                            tp_para_progresso = tp_real
                        else:
                            logger.warning(
                                f"⚠️ TP local inválido e TP real ausente/inválido para cálculo do progresso "
                                f"(tp_local={take_profit}, tp_real={tp_real}, entry={entry_price}). "
                                f"Progresso será calculado em modo ATR."
                            )
                            tp_para_progresso = None
                    except Exception as e:
                        logger.warning(
                            f"⚠️ Falha ao buscar takeProfit real na Bybit para cálculo do progresso: {e}. "
                            f"Progresso será calculado em modo ATR."
                        )
                        tp_para_progresso = None

            # Preferir o takeProfit REAL da posicao (Bybit) para o calculo do progresso,
            # mesmo quando o TP local parecer valido (evita progresso "atrasado").
            try:
                side_l = side.lower()
                entry_f = float(entry_price)
                pos_tp_real = None
                pos = api.get_positions(category="linear", symbol=symbol)
                if pos.get("retCode") == 0:
                    lista = pos.get("result", {}).get("list", []) or []
                    if lista:
                        tp_real_str = lista[0].get("takeProfit")
                        if tp_real_str not in (None, "", "0", "0.0", 0):
                            pos_tp_real = float(tp_real_str)

                if pos_tp_real is not None:
                    tp_real_valido = (side_l == "buy" and pos_tp_real > entry_f) or (side_l == "sell" and pos_tp_real < entry_f)
                    if tp_real_valido:
                        tp_para_progresso = pos_tp_real
            except Exception as e:
                logger.debug(f"Falha ao ler TP real da posicao para calculo do progresso: {e}")

            if tp_para_progresso is not None:
                # 🔹 Progresso clássico baseado no TP (validado)
                tp_progress = float(tp_para_progresso)
                if side.lower() == "buy":
                    progresso_pct = (last_price - entry_price) / (tp_progress - entry_price)
                else:
                    progresso_pct = (entry_price - last_price) / (entry_price - tp_progress)
            else:
                # 🔹 Progresso alternativo baseado em movimento (ATR)
                if trailing_be_liberado and atr_valor_monitor is not None:
                    movimento = abs(last_price - entry_price)
                    # 1 ATR ≈ 100% de progresso
                    progresso_pct = movimento / atr_valor_monitor
                else:
                    progresso_pct = 0.0
                    if not atr_fail_safe_logged:
                        logger.error(
                            f"🛑 FAIL-SAFE: sem ATR válido para progresso em modo ATR | "
                            f"symbol={symbol} | strategy={strategy}. trailing/BE seguem bloqueados."
                        )
                        atr_fail_safe_logged = True

            # clamp universal
            progresso_pct = max(0.0, min(1.5, progresso_pct))
            progresso_pct_cont = progresso_pct
            risk_dist = abs(float(entry_price) - float(stop_loss)) if stop_loss is not None else 0.0
            if side.lower() == "buy":
                movimento_r = max(0.0, last_price - entry_price)
            else:
                movimento_r = max(0.0, entry_price - last_price)
            r_mult = (movimento_r / risk_dist) if risk_dist > 0 else 0.0
            be_r = float(os.getenv("BE_ACTIVATION_R", 1.2))
            trailing_r = float(os.getenv("TRAILING_ACTIVATION_R", 1.0))

            # =====================================================
            # 🎯 ZONA DE DECISÃO PRÓXIMA AO TP (evita saída precoce)
            # =====================================================
            try:
                tp_valido = (
                    take_profit is not None
                    and (
                        (side.lower() == "buy" and float(take_profit) > float(entry_price))
                        or (side.lower() == "sell" and float(take_profit) < float(entry_price))
                    )
                )
            except Exception:
                tp_valido = False

            if tp_valido and progresso_pct >= TP_DECISION_ZONE_PCT:
                try:
                    now_ts = time.time()
                    last_decision_ts = float(trade_state.get("tp_decision_last_ts", 0.0) or 0.0)
                    if now_ts - last_decision_ts >= TP_DECISION_COOLDOWN_SEC:
                        trade_state["tp_decision_last_ts"] = now_ts
                        technical_score = avaliar_score_tecnico(df, side.lower()) if df is not None else 0
                        reversao = detectar_reversao(df) if df is not None else None
                        reversao_contra = (
                            (side.lower() == "buy" and reversao == "reversao_baixa")
                            or (side.lower() == "sell" and reversao == "reversao_alta")
                        )

                        # 1) Reversão perto do alvo => fecha para não devolver lucro
                        if reversao_contra and progresso_pct >= TP_FORCE_EXIT_ZONE_PCT:
                            logger.warning(
                                f"📴 Saída por reversão perto do TP | progresso={progresso_pct:.2%} | "
                                f"score={technical_score}/3 | reversao={reversao}"
                            )
                            try:
                                api.place_order(
                                    category="linear",
                                    symbol=symbol,
                                    side="Sell" if side.lower() == "buy" else "Buy",
                                    orderType="Market",
                                    qty=str(position_size),
                                    reduceOnly=True,
                                    timeInForce="IOC",
                                )
                                break
                            except Exception as e:
                                logger.warning(f"⚠️ Falha ao encerrar por reversão perto do TP: {e}")

                        # 2) Tendência segue forte => estende TP em passos
                        ext_steps = int(trade_state.get("tp_extension_steps", 0) or 0)
                        pode_estender = (
                            TP_EXTENSION_ENABLE
                            and technical_score >= TP_EXTENSION_SCORE_MIN
                            and not reversao_contra
                            and ext_steps < TP_EXTENSION_MAX_STEPS
                        )
                        if pode_estender:
                            tp_atual = float(take_profit)
                            dist_alvo = abs(tp_atual - float(entry_price))
                            passo = max(dist_alvo * TP_EXTENSION_STEP_PCT, TP_EXTENSION_ABS_MIN)
                            if passo > 0:
                                if side.lower() == "buy":
                                    novo_tp = tp_atual + passo
                                else:
                                    novo_tp = tp_atual - passo

                                take_profit = float(novo_tp)
                                trade_state["tp_inicial"] = float(novo_tp)
                                risk_parameters["take_profit"] = float(novo_tp)
                                trade_state["tp_extension_steps"] = ext_steps + 1
                                logger.info(
                                    f"🚀 TP estendido ({trade_state['tp_extension_steps']}/{TP_EXTENSION_MAX_STEPS}) | "
                                    f"TP={tp_atual:.2f}->{novo_tp:.2f} | progresso={progresso_pct:.2%} | "
                                    f"score={technical_score}/3"
                                )

                                # aperta trailing após extensão para proteger lucro
                                if trailing_ja_ativado and trailing_offset and trailing_offset > 0:
                                    trailing_offset = max(
                                        float(trailing_offset) * TP_EXTENSION_TRAILING_TIGHTEN,
                                        TP_EXTENSION_TRAILING_MIN,
                                    )
                except Exception as e:
                    logger.warning(f"⚠️ Falha na zona de decisão do TP: {e}")



            # =====================================================
            # 🔧 Garantir que atr_valor exista (ANTES DA TRANSIÇÃO)
            # =====================================================
            atr_valor = atr_valor_monitor if trailing_be_liberado else None



            # ==============================
            # CONTEXTO DE MERCADO (OBRIGATÓRIO)
            # ==============================

            last_close_m5 = float(df["close"].iloc[-1])

            # ============================================================
            # 🧱 CONFIRMAÇÃO ESTRUTURAL (BUY/SELL simétrico)
            # ============================================================
            try:
                if not trade_state.get("estrutura_confirmada", True):
                    nivel_estrutura = trade_state.get("estrutura_nivel")
                    if nivel_estrutura is not None:
                        nivel_estrutura = float(nivel_estrutura)
                        if side.lower() == "buy" and last_close_m5 > nivel_estrutura:
                            trade_state["estrutura_confirmada"] = True
                            logger.info(
                                f"🧱 Estrutura confirmada | rompeu topo={nivel_estrutura:.2f} | close={last_close_m5:.2f}"
                            )
                        elif side.lower() == "sell" and last_close_m5 < nivel_estrutura:
                            trade_state["estrutura_confirmada"] = True
                            logger.info(
                                f"🧱 Estrutura confirmada | rompeu fundo={nivel_estrutura:.2f} | close={last_close_m5:.2f}"
                            )
            except Exception as e:
                logger.warning(f"⚠️ Falha ao avaliar confirmação estrutural: {e}")

            regime_mercado = detectar_regime_mercado(df) if "detectar_regime_mercado" in globals() else "tendencia"

            tendencia_mercado = detectar_tendencia(df) if "detectar_tendencia" in globals() else (
                "alta" if trade_state["side"] == "buy" else "baixa"
            )

            # ==============================
            # AVALIAÇÃO DE TRANSIÇÃO BREAKOUT
            # ==============================

            erro_tecnico_no_ciclo = False
            try:
                decisao_transicao = avaliar_trade_breakout(
                    trade_state=trade_state,
                    last_price=last_price,
                    last_close_m5=last_close_m5,
                    regime=regime_mercado,
                    tendencia=tendencia_mercado
                )

                acao = decisao_transicao["decisao"]["acao"]
                motivo = decisao_transicao["decisao"].get("motivo", "")

                # 🛡️ BLOQUEAR BREAKOUT_FALHOU_RANGE DURANTE IMUNIDADE
                if trade_state.get("imunidade_breakout", False) and acao == "SAIR_IMEDIATO" and motivo == "retorno_ao_range":
                    logger.info("🛡️ BREAKOUT em janela de imunidade — ignorando retorno ao range")
                    acao = "MANTER"  # força manter durante imunidade

                logger.info(
                    f"🧠 TRANSIÇÃO BREAKOUT | "
                    f"Ação={acao} | Motivo={motivo} | "
                    f"Status={decisao_transicao['classificacao']['status']} | "
                    f"Imunidade={trade_state.get('imunidade_breakout', False)}"
                )

            except Exception as e:
                erro_tecnico_no_ciclo = True
                logger.error(f"❌ Erro técnico no trade_transition_manager: {e}", exc_info=True)
                if trade_state.get("imunidade_breakout", False):
                    logger.warning("🛡️ Erro técnico detectado — trade mantido por imunidade")
                else:
                    logger.warning("🛡️ Erro técnico detectado — trade mantido (acao=MANTER)")
                acao = "MANTER"
                motivo = "erro_tecnico"

            # GATE TÉCNICO SOBERANO: erro técnico nunca pode virar saída.
            if erro_tecnico_no_ciclo:
                acao = "MANTER"



            # ======================================================
            # 🛡️ BREAKOUT: IMUNIDADE E INVALIDAÇÃO ESTRUTURAL
            # ======================================================
            if trade_state.get("modo") == "breakout":
                candle_count = trade_state.get("candle_count", 0)
                imunidade_temporal = candle_count <= BREAKOUT_IMUNIDADE_CANDLES

                if imunidade_temporal:
                    # Nenhuma saída antecipada durante imunidade por candle
                    if acao in ("SAIR_IMEDIATO", "MIGRAR_TENDENCIA", "TIME_STOP"):
                        logger.info("🛡️ Imunidade temporal ativa — ignorando ação de saída antecipada.")
                    acao = "MANTER"
                else:
                    # Após imunidade, só sair por invalidação estrutural clara
                    pivo = _ultimo_pivo_relevante(df, side, lookback=20)
                    last_close_val = float(df["close"].iloc[-1])
                    if _breakout_invalido(side, last_close_val, entry_price, pivo):
                        acao = "SAIR_IMEDIATO"
                        logger.warning(
                            f"⛔ Invalidação estrutural: close={last_close_val:.2f} | "
                            f"entry={entry_price:.2f} | pivo={pivo}"
                        )
                    else:
                        acao = "MANTER"




            # ======================================================
            # 🎯 AÇÕES DEFINIDAS PELO TRADE TRANSITION MANAGER
            # ======================================================

            if acao == "SAIR_IMEDIATO":
                # Em breakout, pullback negativo n?o pode gerar fechamento discricion?rio.
                # Fechamento enquanto negativo s? pode ocorrer via SL/TS da corretora (ou se pre?o j? violou o SL local).
                lucro_atual = last_price - entry_price if side.lower() == "buy" else entry_price - last_price
            
                if lucro_atual <= 0:
                    sl_ref = stop_loss
                    try:
                        sl_ref = float(sl_ref)
                    except Exception:
                        sl_ref = None
            
                    violou_sl = False
                    if sl_ref is not None:
                        if side.lower() == "buy":
                            violou_sl = float(last_price) <= sl_ref
                        else:
                            violou_sl = float(last_price) >= sl_ref
            
                    if not violou_sl:
                        logger.info("??? Trade negativo: bloqueando SAIR_IMEDIATO; mantendo posi??o e deixando SL/TS proteger.")
                        acao = "MANTER"
                    else:
                        logger.warning("? Trade negativo e pre?o j? violou SL: permitindo encerramento/SL.")
            
                if acao == "SAIR_IMEDIATO":
                    if lucro_atual > 0:
                        logger.info(f"?? Trade positivo ({lucro_atual:.2f}) ? ignorando sa?da imediata. Continuando monitoramento.")
                        acao = "MANTER"
                    else:
                        logger.warning("?? Breakout falhou ? sa?da imediata para prote??o de capital.")
            
                        if trade_state.get("encerrado", False):
                            logger.info("?? Trade j? marcado como encerrado. Ignorando nova tentativa de fechamento.")
                            break
            
                        trade_state["encerrado"] = True
            
                        api.place_order(
                            category="linear",
                            symbol=symbol,
                            side="Sell" if side.lower() == "buy" else "Buy",
                            orderType="Market",
                            qty=str(position_size),
                            reduceOnly=True,
                            timeInForce="IOC",
                        )
                        break  # encerra o monitoramento

            elif acao == "TIME_STOP":
                logger.warning("⏱️ Breakout expirou no tempo → apertando stop.")
                # Aqui você pode:
                # - puxar SL para perto
                # - ou deixar trailing assumir

            elif acao == "MIGRAR_TENDENCIA":
                logger.info("🔁 Breakout virou tendência → trailing agressivo liberado.")
                trade_state["modo"] = "tendencia"

            elif acao == "MANTER":
                logger.info("🚀 Breakout saudável → segue plano.")



            # =====================================================
            # 🔧 Garantir que dist_entry e atr_valor existam
            # =====================================================
            
            try:
                dist_entry = abs(last_price - entry_price)
            except:
                dist_entry = 0.0

            # ================================================
            # 🔩 CARREGA CONFIGS DO TRAILING (ANTES DE USAR)
            # ================================================
            TRAILING_ATR_MONITOR_MULT = float(os.getenv("TRAILING_ATR_MONITOR_MULT", 1.8))
            TRAILING_MIN_ENTRY_PCT   = float(os.getenv("TRAILING_MIN_ENTRY_PCT", 0.014))
            TRAILING_ACTIVATION_PCT  = float(os.getenv("TRAILING_ACTIVATION_PCT", 0.30))
            TRAILING_ABSOLUTE_MIN    = float(os.getenv("TRAILING_ABSOLUTE_MIN", 100.0))
            TRAILING_MIN_MOVE_ATR    = float(os.getenv("TRAILING_MIN_MOVE_ATR", 0.5))
            TRAILING_FORCE_NO_STRUCTURE_PCT = float(os.getenv("TRAILING_FORCE_NO_STRUCTURE_PCT", 0.70))

            # Breakeven pode acontecer antes da confirmação estrutural (protege capital).
            BE_ALLOW_BEFORE_STRUCTURE = os.getenv("BE_ALLOW_BEFORE_STRUCTURE", "1") in ("1", "true", "True", "yes", "YES")
            BE_ACTIVATION_PCT = float(os.getenv("BE_ACTIVATION_PCT", 0.25))
            strategy_norm = str(strategy).strip().lower()
            if strategy_norm == "early_trend":
                TRAILING_ATR_MONITOR_MULT = float(os.getenv("EARLY_TREND_TRAILING_ATR_MULT", 2.5))
                TRAILING_MIN_ENTRY_PCT = float(os.getenv("EARLY_TREND_TRAILING_MIN_ENTRY_PCT", TRAILING_MIN_ENTRY_PCT))
                TRAILING_ACTIVATION_PCT = float(os.getenv("EARLY_TREND_TRAILING_ACTIVATION_PCT", 0.35))
                TRAILING_MIN_MOVE_ATR = float(os.getenv("EARLY_TREND_TRAILING_MIN_MOVE_ATR", TRAILING_MIN_MOVE_ATR))
                BE_ACTIVATION_PCT = float(os.getenv("EARLY_TREND_BE_ACTIVATION_PCT", 0.5))
            force_be_after_structure = strategy_norm in ("early_trend", "mean_reversion")
            be_pre_structure_allowed = BE_ALLOW_BEFORE_STRUCTURE and not force_be_after_structure
            BE_ALLOW_BEFORE_STRUCTURE = be_pre_structure_allowed
            BE_OFFSET_PCT = float(os.getenv("BE_OFFSET_PCT", 0.001))

            # ===============================
            # 🔍 STATUS DO TRAILING EM TEMPO REAL
            # ===============================
            try:
                logger.info(
                    f"[TRAILING STATUS] "
                    f"Ativado={trailing_ja_ativado} | "
                    f"Offset={trailing_offset if trailing_offset else 0:.2f} | "
                    f"Progresso={progresso_pct*100:.1f}% | "
                    f"DistânciaEntrada={dist_entry:.2f} | "
                    f"ATR={f'{atr_valor:.2f}' if atr_valor is not None else 'N/A'} | "
                    f"MinMove={f'{(atr_valor*TRAILING_MIN_MOVE_ATR):.2f}' if atr_valor is not None else 'N/A'}"
                )
            except Exception as e:
                logger.warning(f"⚠️ Falha ao exibir status do trailing: {e}")

            # ===========================================================
            # 🔥 TRAILING CONTÍNUO — OPÇÃO A (Ativou → Configura na hora)
            # ===========================================================
            try:
                # progresso já foi calculado antes → usamos ele direto
                progresso_pct_cont = progresso_pct

                # distância da entrada
                if side.lower() == "buy":
                    dist_entry = last_price - entry_price
                else:
                    dist_entry = entry_price - last_price

                logger.info(
                    f"[TRAILING CONTÍNUO] Δ={dist_entry:.2f} | "
                    f"TP%={progresso_pct_cont*100:.1f}% | "
                    f"Ativado={trailing_ja_ativado} | Offset={(trailing_offset or 0):.2f}"
                )

                # 🚫 bloquear trailing abaixo do ponto zero
                if side.lower() == "buy" and last_price < entry_price:
                    logger.info("⛔ BUY: preço abaixo da entrada → bloqueado.")
                    continue

                if side.lower() == "sell" and last_price > entry_price:
                    logger.info("⛔ SELL: preço acima da entrada → bloqueado.")
                    continue


                # ================================
                # 🔧 VALIDAÇÃO DO TP ANTES DO ENVIO (BLINDADA)
                # ================================
                tp_envio = take_profit

                if tp_envio is not None:
                    if side.lower() == "buy" and tp_envio <= last_price:
                        logger.warning(
                            f"⚠️ TP ({tp_envio:.2f}) abaixo do preço atual ({last_price:.2f}). "
                            f"Removendo TP para ativar trailing corretamente."
                        )
                        tp_envio = None

                    if side.lower() == "sell" and tp_envio >= last_price:
                        logger.warning(
                            f"⚠️ TP ({tp_envio:.2f}) acima do preço atual ({last_price:.2f}). "
                            f"Removendo TP para ativar trailing corretamente."
                        )
                        tp_envio = None




                # ▶️ ativação do trailing (INTELIGÊNCIA DO ROBÔ)
                estrutura_confirmada = bool(trade_state.get("estrutura_confirmada", True))
                be_trigger = r_mult >= be_r
                try:
                    lookback_top = int(os.getenv("BE_TOP_LOOKBACK", os.getenv("SR_LOOKBACK", 20)))
                    if df is not None and "high" in df.columns and "low" in df.columns:
                        janela_top = df.tail(lookback_top + 1).iloc[:-1]
                        if not janela_top.empty:
                            if side.lower() == "buy":
                                last_top = float(janela_top["high"].max())
                                if last_price > last_top:
                                    be_trigger = True
                            else:
                                last_bottom = float(janela_top["low"].min())
                                if last_price < last_bottom:
                                    be_trigger = True
                except Exception:
                    pass
                be_progress_ok = progresso_pct_cont >= BE_ACTIVATION_PCT
                if be_trigger and not be_progress_ok:
                    logger.debug(
                        f"Breakeven bloqueado por progresso baixo: "
                        f"{progresso_pct_cont:.2%} < {BE_ACTIVATION_PCT:.0%}"
                    )

                if (
                    trailing_be_liberado
                    and
                    (not breakeven_ja_ativado)
                    and (not BE_ALLOW_BEFORE_STRUCTURE)
                    and estrutura_confirmada
                    and be_trigger
                    and be_progress_ok
                ):
                    try:
                        if side.lower() == "buy":
                            sl_be = max(stop_loss, entry_price * (1.0 + BE_OFFSET_PCT))
                            melhora = sl_be > stop_loss
                        else:
                            sl_be = min(stop_loss, entry_price * (1.0 - BE_OFFSET_PCT))
                            melhora = sl_be < stop_loss

                        if melhora and pode_atualizar_sl_tp(MODO_GESTAO, trailing_ja_ativado):
                            ok_be = atualizar_stop_take(api, symbol, side, sl_be, take_profit, None)
                            if ok_be:
                                stop_loss = sl_be
                                risk_parameters["stop_loss"] = sl_be
                                breakeven_ja_ativado = True
                                logger.info(
                                    f"Breakeven aplicado apos estrutura | SL={stop_loss} | "
                                    f"progresso={progresso_pct_cont:.2%}"
                                )
                            else:
                                logger.warning("Breakeven nao foi aceito pela corretora.")
                    except Exception as e:
                        logger.warning(f"Falha ao aplicar breakeven: {e}")

                # 🛡️ BREAKEVEN ANTES DA ESTRUTURA (opcional)
                if (
                    trailing_be_liberado
                    and
                    (not breakeven_ja_ativado)
                    and BE_ALLOW_BEFORE_STRUCTURE
                    and be_trigger
                    and be_progress_ok
                ):
                    try:
                        if side.lower() == "buy":
                            sl_be = max(stop_loss, entry_price * (1.0 + BE_OFFSET_PCT))
                            melhora = sl_be > stop_loss
                        else:
                            sl_be = min(stop_loss, entry_price * (1.0 - BE_OFFSET_PCT))
                            melhora = sl_be < stop_loss

                        if melhora and pode_atualizar_sl_tp(MODO_GESTAO, trailing_ja_ativado):
                            ok_be = atualizar_stop_take(api, symbol, side, sl_be, take_profit, None)
                            if ok_be:
                                stop_loss = sl_be
                                risk_parameters["stop_loss"] = sl_be
                                breakeven_ja_ativado = True
                                logger.info(f"🛡️ BREAKEVEN aplicado (pré-estrutura) | SL={stop_loss} | progresso={progresso_pct_cont:.2%}")
                            else:
                                logger.warning("⚠️ Breakeven não foi aceito pela corretora.")
                    except Exception as e:
                        logger.warning(f"⚠️ Falha ao aplicar breakeven: {e}")
                # 🧯 Fallback: se o trade andou muito, libera trailing mesmo sem confirmação estrutural
                if (not estrutura_confirmada) and progresso_pct_cont >= TRAILING_FORCE_NO_STRUCTURE_PCT:
                    logger.warning(
                        f"?? Override: progresso alto sem estrutura ({progresso_pct_cont:.2%} >= {TRAILING_FORCE_NO_STRUCTURE_PCT:.0%}) - liberando trailing."
                    )
                    estrutura_confirmada = True

                if not trailing_be_liberado:
                    if not atr_fail_safe_logged:
                        logger.error(
                            f"🛑 FAIL-SAFE: trailing/BE bloqueados por ATR ausente/inválido | "
                            f"symbol={symbol} | strategy={strategy}"
                        )
                        atr_fail_safe_logged = True
                elif not trailing_ja_ativado and r_mult >= trailing_r:
                    if not estrutura_confirmada:
                        logger.info("🧱 Estrutura não confirmada — bloqueando trailing/aperte de SL.")
                    else:
                        trailing_ja_ativado = True
                        logger.info("🔵 TRAILING ATIVADO!")

                        atr_valor = float(atr_valor_monitor)
                        seed = risk_parameters.get("trailing_seed") or risk_parameters.get("trailingOffsetSeed")

                        if seed and seed > 0:
                            trailing_offset = float(seed)
                            logger.info(f"🔧 Seed detectado: {trailing_offset:.2f}")
                        else:
                            trailing_offset = max(
                                atr_valor * (TRAILING_ATR_MONITOR_MULT * 1.3),
                                TRAILING_MIN_ENTRY_PCT * entry_price,
                                atr_valor,
                                TRAILING_ABSOLUTE_MIN
                            )

                        # 🔒 Anti-colagem: trailing nunca maior que a distância até o SL
                        max_offset = abs(entry_price - stop_loss)
                        if trailing_offset > max_offset:
                            trailing_offset = max_offset


                        # ▶️ ENVIO ÚNICO PARA A CORRETORA
                        ok = atualizar_stop_take(
                            api,
                            symbol,
                            side,
                            stop_loss,
                            tp_envio,          # TP pode ser None
                            trailing_offset
                        )

                        if ok:
                            logger.info("🏁 Trailing configurado na corretora.")
                            trailing_nativo_confirmado = bool(
                                hasattr(api, "set_trailing_stop")
                            )

                            # 🔐 AUTORIDADE FINAL
                            if MODO_GESTAO == "CORRETORA" and trailing_nativo_confirmado:
                                trade_state["sl_congelado_corretora"] = True
                                trailing_offset = None  # fire and forget
                                logger.info("🔒 SL agora é 100% da corretora. TP permanece ativo.")
                            elif MODO_GESTAO == "CORRETORA":
                                logger.warning(
                                    "⚠️ Proteção de trailing não é nativa; SL permanece sob controle do robô."
                                )
                        else:
                            logger.warning("⚠️ Trailing NÃO foi aceito pela corretora. Mantendo controle no robô.")

                # A ativação lógica pode ocorrer antes de a proteção nativa ser
                # confirmada. Reenvia enquanto não houver algoId confirmado;
                # sem isso o monitor ficava preso apenas no OCO de SL/TP.
                if (
                    MODO_GESTAO == "CORRETORA"
                    and trailing_ja_ativado
                    and trailing_offset
                    and trailing_offset > 0
                    and not trailing_nativo_confirmado
                    and hasattr(api, "set_trailing_stop")
                ):
                    logger.warning(
                        "🔁 Trailing ativado internamente, mas sem confirmação nativa; tentando novamente | offset=%.4f",
                        float(trailing_offset),
                    )
                    ok_retry = atualizar_stop_take(
                        api, symbol, side, stop_loss, tp_envio, trailing_offset
                    )
                    if ok_retry:
                        trailing_nativo_confirmado = True
                        logger.info("✅ Trailing nativo confirmado após nova tentativa.")
                        trade_state["sl_congelado_corretora"] = True
                        trailing_offset = None
                    else:
                        logger.error(
                            "🛑 Trailing continua sem confirmação; SL não será congelado na corretora."
                        )



                # ▶️ movimento contínuo do SL
                if (
                    MODO_GESTAO == "ROBO"
                    and trailing_ja_ativado
                    and trailing_offset > 0
                    and estrutura_confirmada
                    and pode_atualizar_sl_tp(MODO_GESTAO, trailing_ja_ativado)
                ):
                    if side.lower() == "buy":
                        novo_sl = last_price - trailing_offset
                        if novo_sl > stop_loss:
                            stop_loss = novo_sl
                            atualizar_stop_take(api, symbol, side, stop_loss, take_profit, trailing_offset)



                    else:  # sell
                        novo_sl = last_price + trailing_offset
                        if novo_sl < stop_loss and pode_atualizar_sl_tp(MODO_GESTAO, trailing_ja_ativado):
                            stop_loss = novo_sl
                            risk_parameters["stop_loss"] = novo_sl
                            atualizar_stop_take(api, symbol, side, stop_loss, take_profit, trailing_offset)
                            logger.info(f"🟣 SL trailing movido para {stop_loss:.2f}")


            except Exception as e:
                logger.warning(f"⚠️ Erro no bloco de trailing contínuo: {e}")



            # ======================================================================
            # 🔥 2️⃣ LÓGICA COMPLETA DA PARCIAL (ATIVAÇÃO + EXECUÇÃO)
            # ======================================================================
            if (
                not parcial_ja_feita
                and not parcial_ativada
                and progresso_pct >= PARCIAL_PROGRESS_PCT
            ):
                parcial_ativada = True
                logger.info("🔓 Parcial ativada — progresso >= 55%. Aguardando execução no próximo ciclo.")

            if parcial_ativada and not parcial_ja_feita:
                try:
                    pos = api.get_positions(category="linear", symbol=symbol)
                    position_size = float(pos["result"]["list"][0]["size"])
                except Exception as e:
                    logger.warning(f"⚠️ Falha ao obter posição para parcial: {e}")
                    time.sleep(2)
                    continue

                qty_parcial = round(float(position_size) * float(PARCIAL_FRACTION), 6)

                if qty_parcial < 0.000001:
                    logger.warning("⚠️ Parcial ignorada: qty_parcial abaixo do limite mínimo.")
                    parcial_ja_feita = True
                    continue

                try:
                    qty_corrigida = ajustar_quantidade_para_step_down(session, symbol, qty_parcial)

                    if qty_corrigida <= 0:
                        logger.warning("?? Parcial ignorada: qty_parcial abaixo de min/step (nÆo inflar qty).")
                        parcial_ja_feita = True
                        continue
                    logger.info(f"⚙️ Quantidade parcial ajustada de {qty_parcial} → {qty_corrigida}")

                    if qty_corrigida >= position_size:
                        logger.warning(
                            f"🚫 Parcial ignorada: qty_corrigida ({qty_corrigida}) >= posição total ({position_size})."
                        )
                        parcial_ja_feita = True
                        continue

                    # 📌 Posição antes da parcial
                    try:
                        pos_antes = api.get_positions(category="linear", symbol=symbol)
                        size_antes = float(pos_antes["result"]["list"][0]["size"])
                        logger.info(f"📈 Posição ANTES da parcial: {size_antes}")
                    except:
                        size_antes = position_size

                    # 🔥 EXECUTA PARCIAL (REDUCE ONLY)
                    # ==========================
                    # Filtro fee-aware (evita parcial que nÆo paga taxa)
                    # ==========================
                    if PARCIAL_FEE_AWARE:
                        try:
                            if side.lower() == "buy":
                                move = float(last_price) - float(entry_price)
                            else:
                                move = float(entry_price) - float(last_price)

                            gross_profit = max(0.0, move) * float(qty_corrigida)

                            entry_notional = float(entry_price) * float(qty_corrigida)
                            close_notional = float(last_price) * float(qty_corrigida)

                            entry_fee_alloc = abs(entry_notional) * float(PARCIAL_ASSUME_ENTRY_FEE_RATE)
                            close_fee_est = abs(close_notional) * float(FUTURES_TAKER_FEE_RATE)
                            total_cost = entry_fee_alloc + close_fee_est

                            if gross_profit <= 0:
                                if not parcial_bloqueada_por_taxa:
                                    logger.warning("?? Parcial bloqueada: trade nÆo est  positivo no pre‡o atual.")
                                    parcial_bloqueada_por_taxa = True
                                continue

                            if gross_profit < float(PARCIAL_FEE_MULTIPLIER) * total_cost:
                                if not parcial_bloqueada_por_taxa:
                                    logger.warning(
                                        f"?? Parcial bloqueada por taxa | bruto={gross_profit:.6f} "
                                        f"< {PARCIAL_FEE_MULTIPLIER:.2f}x custos={total_cost:.6f} "
                                        f"(entryFee≈{entry_fee_alloc:.6f}, closeFee≈{close_fee_est:.6f})"
                                    )
                                    parcial_bloqueada_por_taxa = True
                                continue

                            parcial_bloqueada_por_taxa = False
                        except Exception as e:
                            logger.warning(f"?? Falha no filtro fee-aware da parcial (ignorando): {e}")

                    executou_parcial = False

                    if PARCIAL_MAKER_FIRST:
                        try:
                            close_side = "Sell" if side.lower() == "buy" else "Buy"
                            if close_side == "Sell":
                                limit_price = float(last_price) * (1.0 + float(PARCIAL_MAKER_OFFSET_PCT))
                            else:
                                limit_price = float(last_price) * (1.0 - float(PARCIAL_MAKER_OFFSET_PCT))

                            limit_price = ajustar_preco_para_tick(session, symbol, limit_price)

                            resp = api.place_order(
                                category="linear",
                                symbol=symbol,
                                side=close_side,
                                orderType="Limit",
                                qty=f"{qty_corrigida:.6f}",
                                price=str(limit_price),
                                reduceOnly=True,
                                timeInForce="PostOnly",
                            )

                            order_id = None
                            try:
                                if resp and resp.get("retCode") == 0:
                                    order_id = (resp.get("result") or {}).get("orderId")
                            except Exception:
                                order_id = None

                            t0 = time.time()
                            while time.time() - t0 < float(PARCIAL_MAKER_TIMEOUT_SEC):
                                try:
                                    pos_tmp = api.get_positions(category="linear", symbol=symbol)
                                    size_tmp = float(pos_tmp["result"]["list"][0]["size"])
                                    if (float(size_antes) - float(size_tmp)) >= (float(qty_corrigida) * 0.95):
                                        executou_parcial = True
                                        break
                                except Exception:
                                    pass
                                time.sleep(1)

                            if (not executou_parcial) and order_id:
                                try:
                                    api.cancel_order(category="linear", symbol=symbol, orderId=order_id)
                                except Exception as e:
                                    logger.warning(f"?? Falha ao cancelar ordem maker da parcial: {e}")
                        except Exception as e:
                            logger.warning(f"?? Parcial maker-first falhou (fallback para market): {e}")

                    if not executou_parcial:
                        api.place_order(
                            category="linear",
                            symbol=symbol,
                            side="Sell" if side.lower() == "buy" else "Buy",
                            orderType="Market",
                            qty=f"{qty_corrigida:.6f}",
                            reduceOnly=True,
                            timeInForce="IOC",
                        )
                        time.sleep(2)
                    else:
                        time.sleep(1)

                    # 📌 Posição depois da parcial
                    try:
                        pos_depois = api.get_positions(category="linear", symbol=symbol)
                        size_depois = float(pos_depois["result"]["list"][0]["size"])
                        logger.info(
                            f"📉 Posição DEPOIS da parcial: {size_depois} (Δ={size_antes - size_depois:.6f})"
                        )
                    except:
                        size_depois = size_antes - qty_corrigida

                    logger.info(f"🟠 Parcial executada com sucesso: {qty_corrigida} contratos.")

                    # ==========================================================
                    # 🔒 PÓS-PARCIAL — MUDANÇA DE FASE + PROTEÇÃO DE LUCRO
                    # ==========================================================
                    parcial_ja_feita = True
                    trade_state["fase"] = "pos_parcial"
                    trade_state["parcial_executada"] = True

                    # 🛡️ Novo SL = breakeven protegido
                    if side.lower() == "buy":
                        novo_sl = max(stop_loss, entry_price * 1.001)
                    else:
                        novo_sl = min(stop_loss, entry_price * 0.999)

                    stop_loss = novo_sl

                    # 🧠 Mantém TP (ou deixa trailing assumir)
                    take_profit = trade_state["tp_inicial"]

                    # 🚀 Atualiza imediatamente na corretora
                    offset_envio = None if trade_state.get("sl_congelado_corretora") else trailing_offset

                    atualizar_stop_take(
                        api,
                        symbol,
                        side,
                        stop_loss,
                        take_profit,
                        offset_envio
                    )

                    tp_tmp = take_profit
                    if take_profit is None:
                        take_profit = 0.0

                    logger.info(
                        f"🧱 Pós-parcial aplicado | "
                        f"Fase=pos_parcial | SL ajustado={stop_loss:.2f} | "
                        f"TP mantido={take_profit:.2f}"
                    )


                except Exception as e:
                    logger.error(f"❌ Erro ao executar parcial corrigida: {e}", exc_info=True)


            # 🧠 DEBUG TRAILING: mostra as condições de ativação
            try:
                dist_entry_debug = abs(last_price - entry_price)
                atr_debug_txt = f"{atr_valor_monitor:.2f}" if atr_valor_monitor is not None else "N/A"
                min_move_debug_txt = (
                    f"{(atr_valor_monitor*TRAILING_MIN_MOVE_ATR):.2f}"
                    if atr_valor_monitor is not None
                    else "N/A"
                )
                logger.info(
                    f"🐍 TRAILING CHECK → ΔPreço={dist_entry_debug:.2f} | "
                    f"Necessário: {entry_price*TRAILING_MIN_ENTRY_PCT:.2f} "
                    f"ou {min_move_debug_txt} | "
                    f"Ativação TP%={TRAILING_ACTIVATION_PCT*100:.1f}% | "
                    f"Progresso={progresso_pct:.2%} | ATR={atr_debug_txt}"
                )
            except Exception as e:
                logger.warning(f"⚠️ Falha ao logar debug do trailing: {e}")

            # === 5️⃣ ATUALIZAÇÃO DE SL/TP ===
            if MODO_GESTAO == "ROBO":

                posicoes = api.get_positions(category="linear", symbol=symbol)
                posicao = next(
                    (p for p in posicoes.get("result", {}).get("list", []) if p.get("symbol") == symbol),
                    None
                )

                enviar_update = True
                if posicao:
                    sl_atual = float(posicao.get("stopLoss") or 0)
                    tp_atual = float(posicao.get("takeProfit") or 0)
                    ts_atual = float(posicao.get("trailingStop") or 0)

                    def quase_igual(a, b, tolerancia=0.5):
                        try:
                            return abs(float(a) - float(b)) < tolerancia
                        except:
                            return False

                    if (
                        quase_igual(sl_atual, stop_loss)
                        and quase_igual(tp_atual, take_profit)
                        and quase_igual(ts_atual or 0, trailing_offset or 0)
                    ):
                        enviar_update = False

                if trade_state.get("sl_congelado_corretora"):
                    logger.debug("⛔ SL controlado pela corretora. Update bloqueado.")
                else:
                    if enviar_update and pode_atualizar_sl_tp(MODO_GESTAO, trailing_ja_ativado):
                        atualizar_stop_take(api, symbol, side, stop_loss, take_profit, trailing_offset)




            # === 6️⃣ ENCERRAMENTO OU TIMEOUT ===
            if time.time() - tempo_inicio > TEMPO_MAX_MONITORAMENTO:
                logger.info("⏳ Tempo máximo de monitoramento atingido — verificando status da posição.")

                try:
                    posicoes = api.get_positions(category="linear", symbol=symbol)
                    em_posicao = any(
                        abs(float(p.get("size") or p.get("positionAmt") or 0)) > 0
                        for p in posicoes.get("result", {}).get("list", [])
                    )

                    if not em_posicao:
                        logger.info("🚩 Posição encerrada na Bybit. Finalizando monitoramento.")
                        break
                    else:
                        logger.info("✅ Posição ainda aberta — ignorando timeout e continuando monitoramento.")
                        tempo_inicio = time.time()
                except Exception as e:
                    logger.warning(f"⚠️ Falha ao checar posição durante timeout: {e}")
                    tempo_inicio = time.time()

            # (1) LOGA a resposta completa da Bybit ao buscar posições
            # GATE TÉCNICO SOBERANO: este bloco não deve encerrar trade por falso negativo/erro técnico.
            try:
                posicoes = api.get_positions(category="linear", symbol=symbol)
                try:
                    logger.warning(f"[DEBUG] Resposta completa get_positions: {json.dumps(posicoes, indent=2)}")
                except Exception as e:
                    logger.warning(f"[DEBUG] Não foi possível serializar get_positions: {e}")

                if posicoes.get("retCode") == 0:
                    em_posicao = any(
                        abs(float(p.get("size") or p.get("positionAmt") or 0)) > 0
                        for p in posicoes.get("result", {}).get("list", []) or []
                    )
                    logger.warning(f"[DEBUG] em_posicao calculado: {em_posicao}")

                    if not em_posicao:
                        logger.warning(
                            "🛡️ Erro técnico detectado — possível falso negativo de posição; trade mantido (watchdog confirmará fechamento)."
                        )
                else:
                    logger.warning(
                        f"🛡️ Erro técnico detectado — get_positions retCode={posicoes.get('retCode')}; trade mantido."
                    )
            except Exception as e:
                logger.warning(f"🛡️ Erro técnico detectado ao consultar posições — trade mantido: {e}")

            # 🔁 BLOCO INTERMEDIÁRIO ENTRE CANDLES
            try:
                df_refresh = get_market_data_bybit(
                    symbol,
                    api,
                    interval="5",
                    limit=200
                )
                if df_refresh is not None and not df_refresh.empty:
                    df_refresh = calculate_indicators(df_refresh).bfill().ffill()
                    new_price = float(df_refresh["close"].iloc[-1])
                    new_rsi = float(df_refresh["RSI"].iloc[-1])

                    logger.info(
                        f"📊 Atualização intermediária: Preço={new_price:.2f} | RSI={new_rsi:.2f}"
                    )

                    if ordem_ainda_aberta(api, symbol):
                        if MODO_GESTAO == "ROBO":
                            if not trade_state.get("sl_congelado_corretora"):
                                if trade_state.get("estrutura_confirmada", True):
                                    stop_loss = ajustar_trailing_stop_condicional(
                                        api=api,
                                        symbol=symbol,
                                        entry_price=entry_price,
                                        stop_loss=stop_loss,
                                        take_profit=take_profit,
                                        trailing_offset=trailing_offset,
                                        side=side,
                                        preco_atual=new_price,
                                        estrutura_confirmada=True,
                                    )
                                else:
                                    logger.info(
                                        "🧱 Estrutura não confirmada — bloqueando trailing/aperte de SL (intermediário)."
                                    )


            except Exception as e:
                logger.warning(f"⚠️ Falha ao atualizar monitoramento intermediário: {e}")

            time.sleep(5)

        except Exception as e:
            logger.error(f"❌ Erro inesperado durante monitoramento: {e}", exc_info=True)
            time.sleep(15)

    logger.info("✅ Monitoramento finalizado. Resetando estado do trade.")

    try:
        side_cooldown = trade_state.get("side")
        if str(side_cooldown or "").lower() not in ("buy", "sell"):
            side_cooldown = side
        _aplicar_cooldown_breakout(
            symbol,
            float(entry_price),
            trade_state.get("last_price"),
            side_cooldown,
            strategy,
        )
    except Exception as e:
        logger.warning(f"[BREAKOUT] Falha ao aplicar cooldown: {e}")

    trade_state.clear()
    trade_state.update({
        "sl_inicial": None,
        "tp_inicial": None,
        "congelado": False,
        "sl_congelado_corretora": False,
        "parcial_executada": False,
        "fase": "entrada",
        "estrutura_confirmada": False,
        "estrutura_nivel": None,
        "estrutura_tipo": None,
        "estrutura_lookback": None,
        "encerrado": False
    })

    try:
        if os.path.exists(get_lock_path(symbol)):
            remove_lock_fisico(symbol)
            logger.info(f"🔓 Lock físico removido ({get_lock_path(symbol)}). Pronto para nova entrada.")
    except Exception as e:
        logger.error(f"❌ Falha ao remover lock físico: {e}")

