import logging
logger = logging.getLogger(__name__)

import time
import os
import threading
import json
import pandas as pd
from datetime import datetime
from compra1 import session
from utils.indicadores import calculate_indicators
from api_handlers.market_data import get_market_data_bybit
from banco_trade.db_trades import salvar_trade_sqlite
from gestor_de_trade import (
    ajustar_trailing_stop_condicional,
    avaliar_score_tecnico,
    detectar_reversao,
    processar_ajustes_da_ia,
    calcular_lucro_e_registrar,
    parse_timestamp_safe,
)
from utils.logica_stop_take_adaptativo import calcular_stop_take_adaptativo
from utils.utils_telegram import send_telegram_message  # se já não estiver importado


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
# 🧩 Utilitários de ordens
# =========================
class OrdemUtils:
    @staticmethod
    def get_entry_price(symbol, order_id=None):
        """
        Busca o preço de entrada (avgPrice) da ordem no histórico da Bybit.
        Se não achar, busca na posição aberta.
        """
        try:
            res = session.get_order_history(category="linear", symbol=symbol, limit=20)
            if res.get("retCode") == 0 and "list" in res["result"]:
                for order in res["result"]["list"]:
                    if order_id:
                        if str(order.get("orderId")) == str(order_id):
                            price_str = order.get("avgPrice")
                            if price_str:
                                price = float(price_str)
                                if price > 0:
                                    logging.info(f"✅ Entry price recuperado do histórico: {price} para order_id={order_id}")
                                    return price
                    else:
                        price_str = order.get("avgPrice")
                        if price_str:
                            price = float(price_str)
                            if price > 0:
                                logging.info(f"✅ Entry price recuperado do histórico (sem order_id): {price}")
                                return price

            # Fallback: busca em posição aberta
            pos = session.get_positions(category="linear", symbol=symbol)
            if pos.get("retCode") == 0 and "list" in pos["result"] and len(pos["result"]["list"]) > 0:
                entry = pos["result"]["list"][0].get("entryPrice")
                if entry:
                    price = float(entry)
                    if price > 0:
                        logging.info(f"⚠️ Entry price recuperado da posição aberta: {price}")
                        return price

        except Exception as e:
            logging.warning(f"⚠️ Fallback falhou ao recuperar preço de entrada: {e}")

        logging.warning(f"❌ Não foi possível recuperar entry_price para {symbol}, order_id={order_id}")
        return None


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
            for p in posicoes["result"]["list"]:
                tamanho = float(p.get("size") or p.get("positionAmt") or 0)
                if tamanho > 0:
                    return True
    except Exception as e:
        logger.warning(f"⚠️ Falha ao checar posição ativa: {e}")
    return False



def atualizar_stop_take(api, symbol, side, novo_sl, novo_tp, trailing_offset=None):
    """
    Atualiza SL/TP de forma segura:
    - Corrige TP/SL invertidos automaticamente conforme o lado.
    - Ajusta valores conforme tickSize da Bybit.
    - Garante formato decimal correto (sem notação científica).
    - Evita o erro 10001 e 34040.
    """
    try:
        pos = api.get_positions(category="linear", symbol=symbol)
        if pos.get("retCode") != 0:
            logger.warning(f"⚠️ Falha ao obter posições para {symbol}: {pos.get('retMsg')}")
            return False

        lista = pos.get("result", {}).get("list", [])
        if not lista:
            logger.warning(f"⚠️ Nenhuma posição ativa para {symbol}.")
            return False

        atual = lista[0]
        sl_atual = float(atual.get("stopLoss") or 0)
        tp_atual = float(atual.get("takeProfit") or 0)
        close_algos = atual.get("closeOrderAlgo") or []
        protecao_confirmada_okx = isinstance(close_algos, list) and any(
            isinstance(algo, dict)
            and (algo.get("slTriggerPx") or algo.get("tpTriggerPx"))
            and str(algo.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")
            for algo in close_algos
        )
        entry_price = float(atual.get("entryPrice") or 0)

        # --- Evita TP invertido ---
        if side.lower() == "buy" and novo_tp <= entry_price:
            novo_tp = entry_price + abs(entry_price * 0.003)
        elif side.lower() == "sell" and novo_tp >= entry_price:
            novo_tp = entry_price - abs(entry_price * 0.003)

        # --- Ajusta conforme tickSize ---
        tick_size = 0.1
        try:
            info = api.get_instruments_info(category="linear", symbol=symbol)
            tick_size = float(info["result"]["list"][0]["priceFilter"]["tickSize"])
        except Exception:
            pass

        def ajustar_tick(valor):
            casas = str(tick_size)[::-1].find('.')
            return round(round(valor / tick_size) * tick_size, casas)

        novo_sl = ajustar_tick(novo_sl)
        novo_tp = ajustar_tick(novo_tp)

        # <-- Inclua o log aqui:
        logger.debug(f"[DEBUG] Atualizando SL/TP para {symbol}: SL={novo_sl}, TP={novo_tp}, trailing={trailing_offset}, entry={entry_price}")

        # --- Evita reenvio desnecessário ---
        if protecao_confirmada_okx and abs(sl_atual - novo_sl) < tick_size and abs(tp_atual - novo_tp) < tick_size:
            logger.debug("⏩ SL/TP já atualizados. Nenhuma modificação necessária.")
            return True
        if not protecao_confirmada_okx:
            logger.warning("SL/TP nao confirmado pela OKX; forcando envio.")

        payload = {
            "category": "linear",
            "symbol": symbol,
            "stopLoss": f"{novo_sl:.2f}",
            "takeProfit": f"{novo_tp:.2f}",
            "slTriggerBy": "LastPrice",
            "tpTriggerBy": "LastPrice",
            "triggerDirection": 1 if side.lower() == "buy" else 2,
        }

        if trailing_offset and trailing_offset > 0:
            payload["trailingStop"] = f"{trailing_offset:.2f}"

        api.set_trading_stop(**payload)
        logger.info(f"✅ SL/TP atualizados com sucesso: {payload}")
        return True

    except Exception as e:
        logger.error(f"❌ Erro ao atualizar SL/TP: {e}", exc_info=True)
        return False






def monitorar_ordem_ate_encerrar(trade_id, symbol, position_size, entry_price, side, api, risk_parameters, indicators):
    if not trade_id:
        logger.warning("trade_id ausente. Abortando monitoramento.")
        return

    if not entry_price or entry_price == 0:
        logger.warning("entry_price ausente. Abortando monitoramento.")
        return

    logger.info(f"🚨 Monitoramento iniciado para trade_id: {trade_id}")
    operacao_em_andamento = True
    TEMPO_MAX_MONITORAMENTO = int(os.getenv("TEMPO_MAX_MONITORAMENTO", 1800))
    tempo_inicio = time.time()

    # Flags
    breakeven_ja_ativado = False
    trailing_ja_ativado = False
    parcial_ja_feita = False

    trailing_offset = 0.0
    ciclo_monitoramento = 0

    while operacao_em_andamento:
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
            df_raw = get_market_data_bybit(symbol, api, interval="1")
            if df_raw is None or df_raw.empty:
                time.sleep(15)
                continue

            df = calculate_indicators(df_raw).bfill().ffill()
            last_price = float(df["close"].iloc[-1])

            # --- SL/TP adaptativo ---
            stop_loss, take_profit, _, _ = calcular_stop_take_adaptativo(
                df, entry_price, side, tipo_stop=risk_parameters.get("tipo_stop")
            )

            # ========================================
            # 🧩 PATCH: Congela TP inicial e aplica piso fixo (3G)
            # ========================================
            if "take_profit_inicial" not in risk_parameters:
                risk_parameters["take_profit_inicial"] = take_profit
                logger.info(f"🧊 TP inicial fixado: {take_profit:.2f}")
            else:
                tp_inicial = risk_parameters["take_profit_inicial"]
                tp_floor = risk_parameters.get("tp_floor", tp_inicial)
                if side.lower() == "buy":
                    take_profit = max(tp_inicial, tp_floor)
                else:
                    take_profit = min(tp_inicial, tp_floor)
                logger.debug(f"📏 TP mantido fixo: {take_profit:.2f} (TP inicial={tp_inicial:.2f}, piso={tp_floor:.2f})")

            # ✅ Evita reenvio desnecessário se variação < 0.1%
            old_sl = risk_parameters.get("stop_loss", 0)
            old_tp = risk_parameters.get("take_profit", 0)
            pos_check = api.get_positions(category="linear", symbol=symbol)
            pos_rows = (pos_check.get("result", {}).get("list", []) or []) if isinstance(pos_check, dict) else []
            close_algos = (pos_rows[0].get("closeOrderAlgo") or []) if pos_rows else []
            protecao_confirmada_okx = isinstance(close_algos, list) and any(
                isinstance(algo, dict)
                and (algo.get("slTriggerPx") or algo.get("tpTriggerPx"))
                and str(algo.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")
                for algo in close_algos
            )

            if protecao_confirmada_okx and abs(stop_loss - old_sl) / entry_price < 0.001 and abs(take_profit - old_tp) / entry_price < 0.001:
                logger.debug("⏩ Pequena variação de SL/TP detectada (<0.1%), ignorando reenvio.")
                continue

            # Só atualiza se realmente mudou
            if not protecao_confirmada_okx:
                logger.warning("Protecao nao confirmada pela OKX; forcando envio de SL/TP.")
            risk_parameters["stop_loss"] = stop_loss
            risk_parameters["take_profit"] = take_profit


            logger.info(
                f"🔄 Ciclo #{ciclo_monitoramento} | Tempo={tempo_decorrido:.1f}s | "
                f"Preço={last_price:.2f} | Entry={entry_price:.2f} | "
                f"SL={stop_loss:.2f} | TP={take_profit:.2f}"
            )

            # === CÁLCULO DO PROGRESSO ===
            if side.lower() == "buy":
                progresso_atual = last_price - entry_price
                progresso_total = take_profit - entry_price
            else:
                progresso_atual = entry_price - last_price
                progresso_total = entry_price - take_profit

            # === BLINDAGEM SL/TP NO MONITORAMENTO ===
            # Busca tickSize da Bybit
            tick_size = 0.01  # valor padrão seguro
            try:
                if hasattr(api, "get_instruments_info"):
                    info = api.get_instruments_info(category="linear", symbol=symbol)
                    tick_size = float(info["result"]["list"][0]["priceFilter"]["tickSize"])
                elif hasattr(api, "get_symbol_info"):
                    info = api.get_symbol_info(symbol=symbol)
                    tick_size = float(info["tickSize"])
                # Se tiver outro método na sua API, acrescente aqui.
            except Exception as e:
                logger.warning(f"[MONITORAMENTO] Falha ao obter tickSize real da Bybit: {e}. Usando valor padrão {tick_size}")

            def _round_to(value, step):
                if step <= 0: return value
                return round(round(float(value) / step) * step, str(step)[::-1].find('.'))

            # Coerência de direção e arredondamento
            if side.lower() == "buy":
                if stop_loss >= entry_price:
                    stop_loss = entry_price * 0.99
                if take_profit <= entry_price:
                    take_profit = entry_price * 1.01
            else:
                if stop_loss <= entry_price:
                    stop_loss = entry_price * 1.01
                if take_profit >= entry_price:
                    take_profit = entry_price * 0.99

            stop_loss = _round_to(stop_loss, tick_size)
            take_profit = _round_to(take_profit, tick_size)

            logger.info(f"🛡️ [MONITORAMENTO] SL/TP prontos para envio: SL={stop_loss}, TP={take_profit}, Entry={entry_price}, Side={side}, Tick={tick_size}")

            # =============================================
            # ⚙️ VALIDAÇÃO E CORREÇÃO AUTOMÁTICA DE SL/TP
            # =============================================
            try:
                
                RRR = float(os.getenv("RRR", 1.9))

                # ---- BUY (compra) ----
                if side.lower() == "buy":
                    if take_profit <= entry_price:
                        logger.warning("⚠️ TP inválido detectado (abaixo da entrada). Corrigindo automaticamente.")
                        dist = abs(entry_price - stop_loss)
                        if dist > entry_price * 0.02:  # trava segurança: máximo 2% de range
                            dist = entry_price * 0.02
                        take_profit = entry_price + dist * RRR
                    if stop_loss >= entry_price:
                        logger.warning("⚠️ SL inválido detectado (acima da entrada). Corrigindo automaticamente.")
                        stop_loss = entry_price - abs(take_profit - entry_price) / RRR

                # ---- SELL (venda) ----
                elif side.lower() == "sell":
                    if take_profit >= entry_price:
                        logger.warning("⚠️ TP inválido detectado (acima da entrada). Corrigindo automaticamente.")
                        take_profit = entry_price - abs(entry_price - stop_loss) * RRR
                    if stop_loss <= entry_price:
                        logger.warning("⚠️ SL inválido detectado (abaixo da entrada). Corrigindo automaticamente.")
                        stop_loss = entry_price + abs(take_profit - entry_price) / RRR

                # ✅ Confirmação final pós-validação (com cor verde)
                logger.info(f"\033[92m✅ [VALIDAÇÃO] SL/TP corrigidos: SL={stop_loss}, TP={take_profit}, Side={side}\033[0m")

                logger.info(f"\033[92m✅ [VALIDAÇÃO] SL/TP corrigidos: SL={stop_loss}, TP={take_profit}, Side={side}\033[0m")

                # ⚙️ Correção de TP fora do range lógico (±5%)
                if abs(take_profit - entry_price) / entry_price > 0.05:
                    ajuste_pct = 0.015 if side.lower() == "buy" else -0.015
                    take_profit = entry_price * (1 + ajuste_pct)
                    logger.warning(f"⚙️ TP fora do range ajustado para {take_profit:.2f}")


                # 🧠 Debug detalhado (mostra cálculo real do candle atual)
                logger.debug(f"DEBUG: entry={entry_price}, SL={stop_loss}, TP={take_profit}, RRR={RRR}, side={side}")

            except Exception as e:
                logger.error(f"❌ Erro ao validar SL/TP: {e}")




            # Sanidade do TP
            if progresso_total <= 0:
                logger.warning("⚠️ TP inválido detectado. Reajustando defensivamente.")
                delta_min = max(indicators.get("ATR", 50) * 2, entry_price * 0.002)
                take_profit = entry_price + delta_min if side.lower() == "buy" else entry_price - delta_min
                progresso_total = abs(take_profit - entry_price)
                progresso_atual = abs(last_price - entry_price)

            progresso_pct = progresso_atual / progresso_total if progresso_total > 0 else 0
            progresso_pct = max(0.0, min(1.0, progresso_pct))

            # === 1️⃣ BREAKEVEN (BE) SEGURO ===
            if (
                not breakeven_ja_ativado
                and progresso_pct >= 0.65  # antes era 0.5
                and progresso_atual > 0
            ):
                # 🧱 Margem de segurança no breakeven (+0.03% no lucro)
                if side.lower() == "buy":
                    novo_sl = entry_price * 1.0003
                else:
                    novo_sl = entry_price * 0.9997

                cond_ok = (
                    (side.lower() == "buy" and novo_sl <= last_price)
                    or (side.lower() == "sell" and novo_sl >= last_price)
                )

                if cond_ok:
                    if abs(last_price - novo_sl) / max(last_price, 1e-9) < 0.001:
                        logger.warning("🚫 BE bloqueado: SL ficaria colado no preço atual.")
                    else:
                        try:
                            posicoes = api.get_positions(category="linear", symbol=symbol)
                            lista = posicoes.get("result", {}).get("list", [])
                            if not lista or abs(float(lista[0].get("size", 0))) <= 0:
                                logger.warning("🚫 Nenhuma posição ativa encontrada. Encerrando monitoramento.")
                                operacao_em_andamento = False
                                break
                        except Exception as e:
                            logger.error(f"❌ Erro ao verificar posição antes de atualizar stops: {e}")
                            operacao_em_andamento = False
                            break
                        
                        # 🚦 Garante coerência do SL antes de enviar (evita erro 10001 da Bybit)
                        try:
                            if side.lower() == "buy" and novo_sl >= last_price:
                                logger.warning(
                                    f"⚠️ SL ({novo_sl:.2f}) >= preço atual ({last_price:.2f}) → recuando SL para preço de entrada - margem."
                                )
                                novo_sl = entry_price * 0.9995  # ligeiramente abaixo da entrada
                            elif side.lower() == "sell" and novo_sl <= last_price:
                                logger.warning(
                                    f"⚠️ SL ({novo_sl:.2f}) <= preço atual ({last_price:.2f}) → ajustando acima da entrada."
                                )
                                novo_sl = entry_price * 1.0005
                        except Exception as e:
                            logger.error(f"⚠️ Falha ao validar coerência do SL: {e}")



                        if atualizar_stop_take(api, symbol, side, novo_sl, take_profit, trailing_offset):
                            logger.info(f"🟢 Breakeven ativado! SL movido para {novo_sl:.2f}")
                            breakeven_ja_ativado = True



            # === 2️⃣ PARCIAL EM 1R ===
            # === 2️⃣ PARCIAL EM 1R ===
            # === 2️⃣ PARCIAL BASEADA NO CAMINHO ATÉ O TAKE ===
            PCT_PARCIAL_TRIGGER = float(os.getenv("PCT_PARCIAL_TRIGGER", 0.70))  # 70% do caminho até o TP
            PCT_PARCIAL_REDUCAO = float(os.getenv("PCT_PARCIAL_REDUCAO", 0.3))   # reduz 30% da posição

            if (
                not parcial_ja_feita
                and progresso_pct >= PCT_PARCIAL_TRIGGER
                and progresso_atual > 0
            ):
                try:
                    # 📏 Calcula o tamanho parcial
                    qty_parcial = round(position_size * PCT_PARCIAL_REDUCAO, 6)
                    qty_corrigida = ajustar_quantidade_para_minimo(session, symbol, qty_parcial)
                    # 🚫 Ignora parcial se ajuste mínimo fecharia toda a posição
                    if qty_corrigida >= position_size:
                        logger.warning(f"🚫 Parcial ignorada: qty_corrigida={qty_corrigida} igual ao total da posição ({position_size}).")
                        continue

                    logger.info(f"⚙️ Quantidade parcial ajustada de {qty_parcial} → {qty_corrigida}")

                    # 🔍 Posição antes da parcial
                    pos_antes = api.get_positions(category="linear", symbol=symbol)
                    size_antes = float(pos_antes["result"]["list"][0]["size"]) if pos_antes["result"]["list"] else 0

                    # ⚠️ Proteção: não reduz se for igual ao total
                    if qty_corrigida >= size_antes:
                        logger.warning(f"🚫 Parcial abortada: qty_corrigida={qty_corrigida} igual ou maior que posição ({size_antes}).")
                        continue

                    # ⚡ Executa a parcial com reduceOnly=True
                    api.place_order(
                        category="linear",
                        symbol=symbol,
                        side="Sell" if side.lower() == "buy" else "Buy",
                        orderType="Market",
                        qty=f"{qty_corrigida:.6f}",
                        timeInForce="GTC",
                        reduceOnly=True
                    )

                    # Aguarda execução e confirma
                    time.sleep(3)
                    pos_depois = api.get_positions(category="linear", symbol=symbol)
                    size_depois = float(pos_depois["result"]["list"][0]["size"]) if pos_depois["result"]["list"] else 0

                    logger.info(f"📉 Parcial concluída: {size_antes:.6f} → {size_depois:.6f} contratos (redução {size_antes - size_depois:.6f})")
                    logger.info(f"🟠 Parcial de {PCT_PARCIAL_REDUCAO*100:.0f}% executada em {PCT_PARCIAL_TRIGGER*100:.0f}% do caminho até o TP")

                    parcial_ja_feita = True
                    time.sleep(5)  # evita conflito com trailing no mesmo ciclo

                except Exception as e:
                    logger.error(f"❌ Erro ao executar parcial: {e}", exc_info=True)


            # === 3️⃣ TRAILING STOP NATIVO BYBIT (ATIVAÇÃO POR % DE LUCRO) ===
            TRAILING_ATR_MONITOR_MULT = float(os.getenv("TRAILING_ATR_MONITOR_MULT", 1.5))
            TRAILING_ACTIVATION_PCT  = float(os.getenv("TRAILING_ACTIVATION_PCT", 0.6))  # ativa após 60% do alvo
            TRAILING_ABSOLUTE_MIN    = float(os.getenv("TRAILING_ABSOLUTE_MIN", 80.0))
            TRAILING_MIN_MOVE_ATR    = float(os.getenv("TRAILING_MIN_MOVE_ATR", 0.5))

            if (
                not trailing_ja_ativado
                and progresso_pct >= TRAILING_ACTIVATION_PCT
                and progresso_atual > 0
            ):
                atr_valor = float(indicators.get("ATR", 50))
                dist_entry = abs(last_price - entry_price)

                # Evita trailing se o preço ainda não andou o suficiente
                if dist_entry < (atr_valor * TRAILING_MIN_MOVE_ATR):
                    logger.info(
                        f"⏸️ Trailing adiado: preço moveu {dist_entry:.2f} < "
                        f"{TRAILING_MIN_MOVE_ATR}×ATR ({atr_valor*TRAILING_MIN_MOVE_ATR:.2f})"
                    )
                    continue

                # Cálculo do offset ideal (com mínimo absoluto)
                trailing_offset = max(
                    atr_valor * TRAILING_ATR_MONITOR_MULT,
                    TRAILING_ABSOLUTE_MIN,
                    atr_valor * 1.0
                )

                logger.info(
                    f"📈 [TRAILING NATIVO] Condições atendidas → Ativando trailing nativo da Bybit"
                    f" | Progresso={progresso_pct:.1%} | Lucro={progresso_atual:.2f}"
                    f" | ATR={atr_valor:.2f} | Offset={trailing_offset:.2f}"
                )

                try:
                    # Espera breve para garantir atualização da Bybit
                    time.sleep(3)

                    # Verifica posição antes de ativar trailing
                    pos = api.get_positions(category="linear", symbol=symbol)
                    lista = pos.get("result", {}).get("list", [])
                    size_pos = float(lista[0].get("size", 0)) if lista else 0

                    if abs(size_pos) <= 0:
                        logger.warning(
                            f"🚫 Trailing cancelado: posição zerada ({size_pos}). "
                            f"Aguardando nova confirmação."
                        )
                        continue  # volta ao loop sem erro

                    # Aplica trailing nativo só se houver posição ativa
                    resp = api.set_trading_stop(
                        category="linear",
                        symbol=symbol,
                        trailingStop=f"{trailing_offset:.2f}",
                        tpTriggerBy="LastPrice",
                        slTriggerBy="LastPrice"
                    )

                    if resp.get("retCode") == 0:
                        trailing_ja_ativado = True
                        logger.info(
                            f"✅ [TRAILING NATIVO ATIVADO] Offset={trailing_offset:.2f} | "
                            f"Ativado em progresso={progresso_pct:.1%} | "
                            f"Resposta: {resp.get('retMsg','OK')}"
                        )
                    else:
                        logger.warning(
                            f"⚠️ [TRAILING NATIVO FALHOU] Código={resp.get('retCode')} | "
                            f"Mensagem={resp.get('retMsg')}"
                        )

                except Exception as e:
                    logger.error(f"❌ Erro ao aplicar trailing nativo: {e}", exc_info=True)

            else:
                if progresso_atual <= 0:
                    logger.info(
                        f"⏸️ Trailing não ativado: operação ainda não está no lucro "
                        f"({progresso_atual:.2f})."
                    )



            # === 4️⃣ AJUSTES VIA IA (mantido e ampliado) ===
            # === 4️⃣ AJUSTES VIA IA (mantido e ampliado com intervalo) ===
            try:
                IA_ADJUST_INTERVAL = int(os.getenv("IA_ADJUST_INTERVAL", 30))  # tempo em ciclos para reanalisar via IA

                # Executa o bloco de IA apenas a cada N ciclos de monitoramento
                if ciclo_monitoramento % IA_ADJUST_INTERVAL == 15:
                    logger.info(f"🧠 [IA] Ciclo {ciclo_monitoramento} — verificando possíveis ajustes automáticos...")

                    _, risk_parameters, encerrar = processar_ajustes_da_ia(
                        api, symbol, side, trade_id, entry_price, position_size, risk_parameters, indicators
                    )

                    # Caso a IA solicite encerramento imediato
                    if encerrar:
                        logger.info("⚠️ IA solicitou encerramento da operação.")
                        operacao_em_andamento = False
                        break

                    # === NOVO BLOCO: AJUSTE DE TP/SL VIA IA ===
                    novo_sl = risk_parameters.get("stop_loss")
                    novo_tp = risk_parameters.get("take_profit")

                    # Aplica somente se a IA retornou valores válidos e diferentes
                    if (
                        novo_sl and novo_tp and novo_sl > 0 and novo_tp > 0
                        and (abs(novo_sl - risk_parameters.get("stop_loss", 0)) > 1e-3
                            or abs(novo_tp - risk_parameters.get("take_profit", 0)) > 1e-3)
                    ):
                        posicoes = api.get_positions(category="linear", symbol=symbol)
                        lista = posicoes.get("result", {}).get("list", [])

                        if lista and float(lista[0].get("size", 0)) > 0:
                            position_idx = lista[0].get("positionIdx", 0)
                            try:
                                resp_ia = api.set_trading_stop(
                                    category="linear",
                                    symbol=symbol,
                                    positionIdx=position_idx,
                                    takeProfit=f"{novo_tp:.2f}",
                                    stopLoss=f"{novo_sl:.2f}",
                                    tpTriggerBy="LastPrice",
                                    slTriggerBy="LastPrice"
                                )

                                # Log detalhado com resposta da Bybit
                                logger.info(f"🤖 [IA Ajuste] Resposta Bybit: {resp_ia}")

                                if resp_ia.get("retCode") == 0:
                                    logger.info(
                                        f"\033[92m✅ [IA] TP/SL atualizados com sucesso → "
                                        f"TP={novo_tp:.2f} | SL={novo_sl:.2f}\033[0m"
                                    )
                                else:
                                    logger.warning(
                                        f"\033[93m⚠️ [IA] Bybit rejeitou ajuste → "
                                        f"Código: {resp_ia.get('retCode')} | Motivo: {resp_ia.get('retMsg')}\033[0m"
                                    )

                            except Exception as e:
                                logger.error(f"\033[91m❌ [IA] Erro ao aplicar ajuste TP/SL: {e}\033[0m", exc_info=True)
                        else:
                            logger.warning("⚠️ [IA] Nenhuma posição ativa encontrada para aplicar ajuste TP/SL.")
                    else:
                        logger.debug("⏸️ [IA] Nenhum ajuste necessário neste ciclo.")
            except Exception as e:
                logger.error(f"\033[91m❌ [IA] Erro no bloco de ajustes: {e}\033[0m", exc_info=True)



            # === 5️⃣ ATUALIZAÇÃO DE SL/TP (mantido) ===
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
                if sl_atual == stop_loss and tp_atual == take_profit and ts_atual == (trailing_offset if trailing_ja_ativado else 0):
                    enviar_update = False

            if enviar_update:
                atualizar_stop_take(api, symbol, side, stop_loss, take_profit, trailing_offset)



            # === 6️⃣ ENCERRAMENTO OU TIMEOUT (inteligente e seguro) ===
            if time.time() - tempo_inicio > TEMPO_MAX_MONITORAMENTO:
                logger.info("⏳ Tempo máximo de monitoramento atingido — verificando status da posição.")

                try:
                    posicoes = api.get_positions(category="linear", symbol=symbol)
                    em_posicao = any(
                        float(p.get("size") or p.get("positionAmt") or 0) > 0
                        for p in posicoes.get("result", {}).get("list", [])
                    )

                    if not em_posicao:
                        logger.info("🚩 Posição encerrada na Bybit. Finalizando monitoramento.")
                        break  # posição já fechada naturalmente (por SL/TP)
                    else:
                        logger.info("✅ Posição ainda aberta — ignorando timeout e continuando monitoramento.")
                        tempo_inicio = time.time()  # reinicia o contador
                        continue  # mantém o loop rodando
                except Exception as e:
                    logger.warning(f"⚠️ Falha ao checar posição durante timeout: {e}")
                    tempo_inicio = time.time()
                    continue




            # (1) LOGA a resposta completa da Bybit ao buscar posições
            posicoes = api.get_positions(category="linear", symbol=symbol)
            try:
                logger.warning(f"[DEBUG] Resposta completa get_positions: {json.dumps(posicoes, indent=2)}")
            except Exception as e:
                logger.warning(f"[DEBUG] Não foi possível serializar get_positions: {e}")

            if posicoes.get("retCode") == 0:
                # (2) LOGA o cálculo booleano
                em_posicao = any(
                    float(p.get("size") or p.get("positionAmt") or 0) > 0
                    for p in posicoes["result"]["list"]
                )
                logger.warning(f"[DEBUG] em_posicao calculado: {em_posicao}")

                if not em_posicao:
                    logger.warning("🚩 [DEBUG] Nenhuma posição ativa identificada, encerrando monitoramento.")

                    profit_loss = last_price - entry_price if side.lower() == "buy" else entry_price - last_price
                    mensagem = (
                        f"✅ ORDEM FECHADA ({side.upper()})\n"
                        f"Ativo: {symbol}\n"
                        f"Qtd: {position_size}\n"
                        f"Entrada: {entry_price:.2f}\n"
                        f"Fechamento: {last_price:.2f}\n"
                        f"Stop-Loss: {risk_parameters.get('stop_loss', 0):.2f}\n"
                        f"Take-Profit: {risk_parameters.get('take_profit', 0):.2f}\n"
                        f"Resultado: {profit_loss:.2f}\n"
                        f"Horário: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
                    )
                    try:
                        send_telegram_message(mensagem)
                    except Exception as e:
                        logger.warning(f"Falha ao enviar mensagem Telegram de fechamento: {e}")

                    operacao_em_andamento = False
                    break
            # === 🔁 NOVO BLOCO: ATUALIZAÇÃO DE MONITORAMENTO ENTRE CANDLES ===
            time.sleep(5)
            
        except Exception as e:
            logger.error(f"❌ Erro inesperado durante monitoramento: {e}", exc_info=True)
            time.sleep(15)

    logger.info("✅ Monitoramento finalizado. Sistema liberado para nova análise.")
    try:
        if os.path.exists(get_lock_path(symbol)):
            remove_lock_fisico(symbol)
            logger.info(f"🔓 Lock físico removido ({get_lock_path(symbol)}). Pronto para nova entrada.")

    except Exception as e:
        logger.error(f"❌ Falha ao remover lock físico: {e}")
