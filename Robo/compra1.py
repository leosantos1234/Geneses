from utils.logger_config import configurar_logger_global
configurar_logger_global("compra1.log")

import logging
logger = logging.getLogger(__name__)

import os
import time   # â¬…ï¸ sobe
import uuid
import hashlib
import hmac
import threading
import json
from datetime import datetime

import requests
import pandas as pd
from dotenv import load_dotenv

from threading import Lock

# utils / projeto
from banco_trade.db_trades import salvar_trade_sqlite   # â¬…ï¸ apenas uma vez
from utils.validar_sl_tp import validar_sl_tp
from leotelegram import send_telegram_message
from utils.timestamp import obter_diferenca_timestamp
from utils.indicadores import calculate_indicators
from utils.utils_market import get_market_data_bybit
from utils.risco_sl_tp import calc_sl_tp
from utils.monitoramento_ordem import esperar_ordem_executada
from utils.logica_stop_take_adaptativo import (
    calcular_stop_take_adaptativo, indicadores_para_dict, detectar_contexto_mercado, PARAMS
)
from utils.trailing_adaptativo import calcular_trailing_adaptativo
from auditoria.guardiao_direcional import inferir_direcao_macro, permitir_operacao
from auditoria.strategy_audit import register_strategy_audit
from utils.risk_guard import (
    evaluate_risk_limits,
    write_kill_switch_flag,
    process_risk_transition,
    register_critical_event,
)
from utils.paper_mode import is_paper_trading_enabled, register_paper_trade
from comandante.hierarquia import avaliar_decisao_comandante
from execution.execution_gate import executar_ordem_com_validacao
from lineage.decision_lineage import create_decision, current_decision_id, finalize_decision, link_trade, record_event
from exchange.factory import active_exchange_name, create_exchange_client
from genesis_runtime import connect_operational_database
# Sistema de entrada por ordem limite
from utils.ordem_limite_entrada import executar_entrada_por_limite
# Import adiado para evitar loop
monitorar_ordem_ate_encerrar = None

# --- ConfiguraÃ§Ãµes da Bybit ---
from dotenv import load_dotenv
import os



API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "False").strip().lower() == "true"
CATEGORY = "linear"
API_IA_URL = os.getenv("API_IA_URL")

# ðŸ”¹ Leitura das variÃ¡veis
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")

# ðŸ”¹ InterpretaÃ§Ã£o correta do modo Testnet
BYBIT_TESTNET_RAW = os.getenv("BYBIT_TESTNET", "False").strip().lower()
BYBIT_TESTNET = BYBIT_TESTNET_RAW in ["1", "true", "yes"]
ACTIVE_EXCHANGE = active_exchange_name().upper()
if ACTIVE_EXCHANGE == "BYBIT":
    logging.info(f"BYBIT_TESTNET interpretado como: {BYBIT_TESTNET} (valor bruto='{BYBIT_TESTNET_RAW}')")
else:
    logging.info(f"Exchange ativa: {ACTIVE_EXCHANGE}")

CATEGORY = "linear"

# ðŸ”— Seleciona URL base conforme o ambiente
URL_BASE_API = "https://api-testnet.bybit.com" if BYBIT_TESTNET else "https://api.bybit.com"

# ðŸ”¹ ConfiguraÃ§Ã£o da IA Comandante
API_IA_URL = os.getenv("API_IA_URL")

# ðŸ†• ConfiguraÃ§Ã£o de entrada por ordem limite
USAR_ORDEM_LIMITE = os.getenv("USAR_ORDEM_LIMITE", "False").strip().lower() in ["true", "1", "yes"]
logging.info(f"ðŸŽ¯ Modo de entrada: {'ORDEM LIMITE' if USAR_ORDEM_LIMITE else 'MARKET ORDER'}")

# ðŸ” Compatibilidade retroativa
URL_BASE = URL_BASE_API

if ACTIVE_EXCHANGE == "BYBIT":
    logging.info(f"Ambiente Bybit configurado: {'TESTNET' if BYBIT_TESTNET else 'PRODUCAO'} -> {URL_BASE_API}")
else:
    logging.info(f"Ambiente configurado para {ACTIVE_EXCHANGE}")



# ============================================================
# ðŸ”’ NORMALIZAÃ‡ÃƒO DE ORDER TYPE PARA BYBIT
# ============================================================

def normalizar_order_type(order_type: str):
    """
    Normaliza orderType para valores vÃ¡lidos da Bybit.
    Se invÃ¡lido â†’ ABORTA.
    """
    if not order_type:
        logging.error("âŒ OrderType ausente. Abortando trade.")
        return None

    order_type = str(order_type).lower().strip()

    tipos_validos = {
        "market": "Market",
        "limit": "Limit",
        "stop": "Stop",
        "stoplimit": "StopLimit",
        "stop_limit": "StopLimit",
    }

    mapeamento_interno = {
        "limit_adaptativa": "Limit",
        "limit_confirmacao": "Limit",   # ðŸ”‘ ESSA LINHA
        "market_adaptativo": "Market",
    }

    if order_type in tipos_validos:
        return tipos_validos[order_type]

    if order_type in mapeamento_interno:
        return mapeamento_interno[order_type]

    logging.error(f"âŒ OrderType invÃ¡lido recebido: '{order_type}'. Abortando trade.")
    return None







# âœ… --- SessÃ£o thread-safe + fÃ¡brica de sessÃ£o local ---
from threading import Lock
lock_bybit = Lock()

def create_bybit_session():
    """Cria o cliente da exchange selecionada no perfil ativo."""
    try:
        sess = create_exchange_client()
        logging.info("Cliente da exchange ativa criado: %s", active_exchange_name().upper())
        return sess
    except Exception as e:
        logging.error(f"âŒ Falha ao criar cliente da exchange ativa: {e}")
        return None




# âœ… SessÃ£o global inicial (usada em chamadas simples)
session = create_bybit_session()
if not session:
    logging.error("ðŸš« Falha ao criar sessÃ£o Bybit inicial. Abortando execuÃ§Ã£o.")
    raise SystemExit("SessÃ£o invÃ¡lida - verifique API_KEY, API_SECRET e BYBIT_TESTNET")


# âœ… FunÃ§Ã£o segura para enviar ordens (usa lock e sessÃ£o local)
def enviar_ordem_segura(params):
    """Envia ordens de forma thread-safe, com sessÃ£o isolada e retry automÃ¡tico."""
    for tentativa in range(3):
        try:
            with lock_bybit:
                sess_local = create_bybit_session()
                logging.info(f"ðŸ“¤ Tentativa {tentativa+1}/3 â†’ {params.get('symbol')} | Side={params.get('side')}")
                resp = sess_local.place_order(**params)

            if not resp or not isinstance(resp, dict):
                logging.warning("âš ï¸ Resposta vazia da Bybit. Recriando sessÃ£o e tentando novamente...")
                try:
                    # ðŸ” Recria sessÃ£o global e local automaticamente
                    globals()["session"] = create_bybit_session()
                    sess_local = create_bybit_session()

                    # Pequeno delay antes do retry
                    time.sleep(1)
                    resp = sess_local.place_order(**params)

                    # Se ainda assim vier vazio, continua o loop
                    if not resp or not isinstance(resp, dict):
                        logging.error("âŒ Segunda tentativa tambÃ©m falhou â€” resposta ainda vazia.")
                        time.sleep(2)
                        continue

                except Exception as e:
                    logging.error(f"âŒ Falha ao recriar sessÃ£o e reenviar ordem: {e}", exc_info=True)
                    time.sleep(2)
                    continue


            if resp.get("retCode", -1) != 0:
                ret_msg = resp.get("retMsg", "Sem mensagem")
                logging.warning(f"âš ï¸ Bybit retornou retCode={resp.get('retCode')} | {ret_msg}")
                time.sleep(2)
                continue

            logging.info("âœ… Ordem enviada com sucesso Ã  Bybit.")
            return resp

        except Exception as e:
            logging.error(f"âŒ Erro ao enviar ordem (tentativa {tentativa+1}/3): {e}", exc_info=True)
            time.sleep(2)

    logging.error("âŒ Todas as tentativas de envio falharam. Bybit nÃ£o respondeu.")
    return None


def enviar_ordem_adaptativa(params):
    """
    Alias explicito do envio real de abertura usado pelo execution gate.
    """
    return enviar_ordem_segura(params)




def testar_tipos_de_saldo():
    for tipo in ["UNIFIED", "CONTRACT"]:  # SPOT REMOVIDO
        try:
            logging.info(f"ðŸ” Testando accountType = {tipo}")
            response = session.get_wallet_balance(accountType=tipo)
            if response.get("retCode") == 0:
                logging.info(f"âœ… Tipo {tipo} FUNCIONA para PERPÃ‰TUO!")
                return tipo, response
        except Exception as e:
            logging.error(f"âŒ Erro ao testar tipo {tipo}: {e}")
    logging.error("âŒ Nenhum tipo de conta compatÃ­vel com PERPÃ‰TUO foi encontrada.")
    return None, None


def detectar_account_type():
    for tipo in ["UNIFIED", "CONTRACT"]:  # SPOT REMOVIDO
        try:
            res = session.get_wallet_balance(accountType=tipo)
            if res.get("retCode") == 0:
                logging.info(f"âœ… Tipo de conta compatÃ­vel com PERPÃ‰TUO detectado: {tipo}")
                return tipo
        except Exception as e:
            logging.warning(f"Falha ao testar tipo {tipo}: {e}")
    logging.error("âŒ Nenhum tipo de conta compatÃ­vel encontrado.")
    return None


def get_balance():
    try:
        timestamp = int(time.time() * 1000) + obter_diferenca_timestamp()
        response = session.get_wallet_balance(accountType="UNIFIED", timestamp=timestamp)
        print("DEBUG SALDO BYBIT:", response)  # <-- Veja exatamente a resposta!
        if response["retCode"] == 0:
            for item in response["result"]["list"]:
                for coin in item["coin"]:
                    if coin["coin"] == "USDT":
                        print("==== DETALHE USDT ====")
                        for k, v in coin.items():
                            print(f"{k}: {v}")
                        # Escolha aqui o campo correto!
                        saldo = float(
                            coin.get("totalAvailableBalance")
                            or coin.get("availableToWithdraw")
                            or coin.get("walletBalance")
                            or coin.get("equity")
                            or 0
                        )
                        logging.info(f"ðŸ’° Saldo USDT (escolhido): {saldo}")
                        return saldo
    except Exception as e:
        logging.error(f"ExceÃ§Ã£o ao consultar saldo: {e}")
    return 0







def generate_signature(params, api_secret=API_SECRET):
    query_string = "&".join([f"{key}={params[key]}" for key in sorted(params)])
    return hmac.new(api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()




def obter_preco_mercado(session=None, symbol=None, candle=None, modo_teste=False):
    if modo_teste and candle:
        logging.debug(f"ðŸ” MODO TESTE ATIVO â€” Retornando candle['close']: {candle['close']}")
        return candle["close"]

    try:
        response = session.get_tickers(category="linear", symbol=symbol)
        logging.debug(f"ðŸ“¦ DEBUG: resposta bruta da Bybit: {response}")

        if response.get("retCode", -1) != 0:
            logging.warning(f"âš ï¸ API Bybit retornou erro: {response.get('retMsg')}")
            return None

        lista_precos = response.get("result", {}).get("list", [])

        if not lista_precos or not isinstance(lista_precos, list):
            logging.error("âŒ Lista de preÃ§os vazia ou invÃ¡lida.")
            return None

        preco_str = lista_precos[0].get("lastPrice")
        if preco_str is None:
            logging.error("âŒ Campo 'lastPrice' ausente.")
            return None

        try:
            preco_float = float(preco_str)
            logging.debug(f"âœ… PreÃ§o de mercado extraÃ­do: {preco_float}")
            return preco_float

        except ValueError:
            logging.error(f"âŒ Erro ao converter 'lastPrice' para float: {preco_str}")
            return None

    except Exception as e:
        logging.error(f"âŒ ExceÃ§Ã£o ao obter preÃ§o de mercado: {e}", exc_info=True)
        return None





def _safe_env_float_local(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except Exception:
        logging.warning(f"âš ï¸ ENV invÃ¡lida {name}={v}. Usando default={default}.")
        return default


def _measure_api_ping_ms(sess, symbol: str):
    try:
        t0 = time.perf_counter()
        sess.get_tickers(category="linear", symbol=symbol)
        return (time.perf_counter() - t0) * 1000.0
    except Exception:
        return None


def _get_spread_and_depth(sess, symbol: str):
    out = {
        "bid": None,
        "ask": None,
        "spread_abs": None,
        "spread_pct": None,
        "depth_qty_top5": None,
    }
    try:
        ob = sess.get_orderbook(category="linear", symbol=symbol, limit=25)
        if ob.get("retCode") == 0:
            b = (ob.get("result", {}) or {}).get("b", []) or []
            a = (ob.get("result", {}) or {}).get("a", []) or []
            if b and a:
                bid = float(b[0][0])
                ask = float(a[0][0])
                spread_abs = max(ask - bid, 0.0)
                mid = (ask + bid) / 2 if (ask + bid) > 0 else 0.0
                spread_pct = (spread_abs / mid * 100.0) if mid > 0 else None
                depth_b = sum(float(x[1]) for x in b[:5]) if b else 0.0
                depth_a = sum(float(x[1]) for x in a[:5]) if a else 0.0
                out.update(
                    {
                        "bid": bid,
                        "ask": ask,
                        "spread_abs": spread_abs,
                        "spread_pct": spread_pct,
                        "depth_qty_top5": depth_b + depth_a,
                    }
                )
                return out
    except Exception:
        pass

    try:
        t = sess.get_tickers(category="linear", symbol=symbol)
        if t.get("retCode") == 0:
            item = (t.get("result", {}) or {}).get("list", [{}])[0]
            bid = float(item.get("bid1Price") or 0.0)
            ask = float(item.get("ask1Price") or 0.0)
            if bid > 0 and ask > 0:
                spread_abs = max(ask - bid, 0.0)
                mid = (ask + bid) / 2
                spread_pct = (spread_abs / mid * 100.0) if mid > 0 else None
                out.update(
                    {
                        "bid": bid,
                        "ask": ask,
                        "spread_abs": spread_abs,
                        "spread_pct": spread_pct,
                    }
                )
    except Exception:
        pass

    return out


def solicitar_tipo_stop_comandante(contexto_mercado, indicadores, score_ia=None, forcar_tipo=None, votos_agentes=None):
    import requests
    try:
        payload = {
            "contexto_mercado": contexto_mercado,
            "indicadores": indicadores,
            "score_ia": score_ia,
            "forcar_tipo": forcar_tipo,
            "votos_agentes": votos_agentes,
        }
        resp = requests.post(f"{API_IA_URL}/comandante_tipo_stop", json=payload, timeout=4)
        if resp.status_code == 200:
            return resp.json().get("tipo_stop")
    except Exception as e:
        logging.warning(f"[Comandante] Fallback para ATR (erro ao chamar endpoint): {e}")
    return "atr"  # fallback seguro!




import os

ATIVO = os.getenv("ATIVOS", "BTCUSDT").strip().upper()  # Le do .env, se nÃ£o existir usa BTCUSDT

def cancelar_todas_ordens(symbol=None):
    try:
        symbol = symbol.strip().upper() if symbol else ATIVO
        logging.info(f"ðŸ” Cancelando ordens abertas para o sÃ­mbolo: {symbol}")

        # Consulta as ordens abertas com sÃ­mbolo especÃ­fico
        response = session.get_open_orders(category="linear", symbol=symbol)

        if response.get("retCode") == 0 and response.get("result", {}).get("list"):
            for order in response["result"]["list"]:
                order_id = order.get("orderId")
                if order_id:
                    session.cancel_order(category="linear", symbol=symbol, orderId=order_id)
                    logging.info(f"âœ… Ordem {order_id} cancelada.")
        else:
            logging.warning(f"âš ï¸ Nenhuma ordem aberta encontrada ou erro na resposta: {response}")
    except Exception as e:
        logging.error(f"âŒ Erro ao cancelar ordens abertas: {e}")




def get_saldo_disponivel(symbol: str) -> float:
    try:
        res = session.get_wallet_balance(accountType="UNIFIED")
        if res.get("retCode") != 0: return 0.0
        for item in res["result"]["list"]:
            for coin in item["coin"]:
                if coin["coin"] == "USDT":
                    return float(
                        coin.get("totalAvailableBalance")
                        or coin.get("availableToWithdraw")
                        or coin.get("walletBalance")
                        or coin.get("equity")
                        or 0.0
                    )
        return 0.0
    except Exception:
        return 0.0





def get_entry_price_fallback(symbol, order_id, entry_price, tentativas=3, delay=2):
    try:
        for tent in range(tentativas):
            res = session.get_order_history(category="linear", symbol=symbol, limit=20)
            if res.get("retCode") == 0 and "list" in res["result"]:
                for order in res["result"]["list"]:
                    if order.get("orderId") == order_id:
                        price_str = order.get("avgPrice")
                        if price_str:
                            price = float(price_str)
                            if price > 0:
                                logging.info(f"âœ… PreÃ§o recuperado pelo get_order_history: {price}")
                                return price
            # Aguarda antes de tentar de novo (caso avgPrice nÃ£o esteja disponÃ­vel imediatamente)
            if tent < tentativas - 1:
                time.sleep(delay)

        # Fallback: consulta posiÃ§Ã£o aberta
        pos = session.get_positions(category="linear", symbol=symbol)
        if pos.get("retCode") == 0 and "list" in pos["result"] and pos["result"]["list"]:
            entry = pos["result"]["list"][0].get("entryPrice")
            if entry:
                price = float(entry)
                if price > 0:
                    logging.info(f"âš ï¸ PreÃ§o recuperado pelo get_positions: {price}")
                    return price

        logging.warning(f"âŒ Falha ao recuperar preÃ§o apÃ³s {tentativas} tentativas.")
    except Exception as e:
        logging.warning(f"âš ï¸ Fallback falhou ao recuperar preÃ§o de entrada: {e}")

    logging.warning(f"âš ï¸ Retornando entry_price passado como fallback: {entry_price}")
    return entry_price  # Ãºltimo recurso






def ajustar_qty_por_saldo(saldo_disponivel: float, preco_entrada: float, margem_porcentagem: float = 0.1) -> float:
    # margem_porcentagem: % do saldo que vocÃª quer usar para essa ordem (ex: 10%)
    max_valor_ordem = saldo_disponivel * margem_porcentagem
    qty_max = max_valor_ordem / preco_entrada
    return round(qty_max, 6)  # ajuste conforme o mÃ­nimo da corretora



def verificar_sessao_ativa():
    """Verifica se a sessÃ£o da Bybit estÃ¡ ativa, caso contrÃ¡rio recria automaticamente."""
    global session
    try:
        resp = session.get_server_time()
        if not resp or resp.get("retCode") != 0:
            logging.warning("âš ï¸ SessÃ£o Bybit inativa ou expirada. Recriando sessÃ£o...")
            session = create_bybit_session()
            return False
        return True
    except Exception as e:
        logging.warning(f"âš ï¸ Erro ao testar conexÃ£o com Bybit: {e}. Tentando reconectar...")
        session = create_bybit_session()
        return False


# ============================================================
# ðŸ§  NOVA FUNÃ‡ÃƒO UNIFICADA DE EXECUÃ‡ÃƒO DE ORDENS
# ============================================================

def aplicar_trading_stop_com_retry(session, symbol, stop_loss, take_profit, tentativas=3, delay=2):
    """
    Aplica SL/TP via set_trading_stop de forma robusta, com retries e validaÃ§Ã£o.
    Evita ranges invÃ¡lidos, valores zerados e garante que a posiÃ§Ã£o jÃ¡ existe.
    """
    try:
        if not symbol:
            logging.warning("âš ï¸ SÃ­mbolo nÃ£o informado para aplicar_trading_stop_com_retry.")
            return {"retCode": -1, "retMsg": "SÃ­mbolo invÃ¡lido"}

        # ValidaÃ§Ã£o numÃ©rica bÃ¡sica
        stop_loss = float(stop_loss or 0)
        take_profit = None if take_profit in (None, "", "0", "0.0", 0) else float(take_profit)
        if stop_loss <= 0:
            logging.warning(f"Æ’sÃ¿â€¹Ã·? SL invÃ‡Â­lido Æ’Ã…' SL={stop_loss}. Cancelando.")
            return {"retCode": -1, "retMsg": "SL invÃ‡Â­lido"}
            logging.warning(f"âš ï¸ SL/TP invÃ¡lidos â†’ SL={stop_loss}, TP={take_profit}. Cancelando.")
            return {"retCode": -1, "retMsg": "Valores invÃ¡lidos"}

        for tentativa in range(tentativas):
            try:
                pos = session.get_positions(category="linear", symbol=symbol)
                pos_list = pos.get("result", {}).get("list", [])
                if not pos_list or float(pos_list[0].get("size", 0)) <= 0:
                    logging.info(f"â³ PosiÃ§Ã£o ainda nÃ£o aberta ({tentativa+1}/{tentativas}). Aguardando {delay}s...")
                    time.sleep(delay)
                    continue

                position = pos_list[0]
                position_idx = position.get("positionIdx", 0)
                # Recalcula a validade dos gatilhos usando o ultimo preco.
                # Isso evita o 51278 quando a entrada a mercado desliza.
                try:
                    ticker_rows = session.get_tickers(category="linear", symbol=symbol).get("result", {}).get("list", [])
                    last_price = float(ticker_rows[0].get("lastPrice") or 0) if ticker_rows else 0.0
                    if last_price:
                        tick_rows = session.get_instruments_info(category="linear", symbol=symbol).get("result", {}).get("list", [])
                        tick = float(((tick_rows[0].get("priceFilter") or {}).get("tickSize") or 0.001)) if tick_rows else 0.001
                        buffer = max(tick * 2, last_price * 0.0002)
                        is_sell = str(position.get("side") or position.get("posSide") or "").lower() in ("sell", "short")
                        if is_sell:
                            stop_loss = max(float(stop_loss), last_price + buffer)
                            if take_profit is not None:
                                take_profit = min(float(take_profit), last_price - buffer)
                        else:
                            stop_loss = min(float(stop_loss), last_price - buffer)
                            if take_profit is not None:
                                take_profit = max(float(take_profit), last_price + buffer)
                except Exception as price_exc:
                    logging.debug("[PROTECAO] ajuste pelo ultimo preco indisponivel: %s", price_exc)
                payload = {
                    "category": "linear",
                    "symbol": symbol,
                    "positionIdx": position_idx,
                    "stopLoss": f"{stop_loss:.2f}",
                    "slTriggerBy": "LastPrice",
                }
                if take_profit is not None:
                    payload["takeProfit"] = f"{take_profit:.2f}"
                    payload["tpTriggerBy"] = "LastPrice"
                resp = session.set_trading_stop(**payload)

                if resp.get("retCode") == 0:
                    # HTTP 0 nao basta: confirma o OCO real na posicao.
                    confirmado = False
                    for _ in range(4):
                        time.sleep(0.35)
                        ver = session.get_positions(category="linear", symbol=symbol)
                        vrows = ver.get("result", {}).get("list", []) if isinstance(ver, dict) else []
                        vpos = next((row for row in vrows if abs(float(row.get("size") or 0)) > 0), None)
                        algos = (vpos or {}).get("closeOrderAlgo") or []
                        ativos = [a for a in algos if str(a.get("state") or "").lower() not in ("canceled", "cancelled", "order_failed")]
                        has_sl = any(a.get("slTriggerPx") not in (None, "", "0", "0.0", 0) for a in ativos)
                        has_tp = any(a.get("tpTriggerPx") not in (None, "", "0", "0.0", 0) for a in ativos)
                        if has_sl and has_tp:
                            confirmado = True
                            break
                    if confirmado:
                        take_profit = take_profit or 0.0
                        logging.info(f"TP/SL confirmados pela OKX (tentativa {tentativa+1}) -> TP={take_profit:.2f} | SL={stop_loss:.2f}")
                        return resp
                    logging.warning("OKX aceitou a chamada, mas nao confirmou closeOrderAlgo com SL e TP.")
                else:
                    logging.warning(f"âš ï¸ Tentativa {tentativa+1} falhou: {resp.get('retMsg')}")
                    time.sleep(delay)
            except Exception as e:
                logging.warning(f"âŒ Erro na tentativa {tentativa+1} de aplicar SL/TP: {e}")
                time.sleep(delay)

        logging.error("ðŸš« Falha ao aplicar SL/TP apÃ³s mÃºltiplas tentativas.")
        return {"retCode": -1, "retMsg": "Falha apÃ³s retries"}

    except Exception as e:
        logging.error(f"âŒ ExceÃ§Ã£o em aplicar_trading_stop_com_retry: {e}", exc_info=True)
        return {"retCode": -1, "retMsg": str(e)}


def validar_bloqueio_direcional(side: str, tendencia_macro: str):
    """
    Bloqueia operaÃ§Ã£o contra a tendÃªncia macro dominante.
    Retorna permitido (bool) e motivo explicativo (str).
    """
    if not tendencia_macro:
        return True, "Sem tendÃªncia macro definida"

    side = side.lower()
    tendencia_macro = tendencia_macro.lower()

    if tendencia_macro == "sell" and side == "buy":
        return False, "Bloqueado: mercado em tendÃªncia de VENDA"

    if tendencia_macro == "buy" and side == "sell":
        return False, "Bloqueado: mercado em tendÃªncia de COMPRA"

    return True, "DireÃ§Ã£o alinhada com tendÃªncia macro"


def detectar_regime_unificado(macro_regime=None, regime=None, contexto_mercado=None):
    """
    Consolida as fontes atuais de regime em uma única referência.
    Prioridade:
    1. regime
    2. macro_regime
    3. contexto_mercado
    """

    def _normalizar(valor):
        valor = str(valor or "").strip().lower()
        if not valor:
            return None
        if valor in {"lateral", "range", "ranging", "lateralizacao"}:
            return "lateral"
        if valor in {"caos", "choppy", "volatil", "volatilidade", "alta_volatilidade"}:
            return "caos"
        if valor in {"tendencia", "buy", "sell", "bull", "bear", "trend", "trending"}:
            return "tendencia"
        return None

    return (
        _normalizar(regime)
        or _normalizar(macro_regime)
        or _normalizar(contexto_mercado)
        or "tendencia"
    )


def ajustar_lote_por_probabilidade(qty_base, prob):
    try:
        qty_base = float(qty_base)
        prob = float(prob)
    except Exception:
        return qty_base

    if prob < 0.55:
        return 0.0
    if prob < 0.60:
        return qty_base * 0.5
    if prob < 0.70:
        return qty_base * 0.8
    return qty_base


def log_veto_entrada(contexto: dict, motivos: list):
    """
    Log estruturado para veto de entrada.
    NÃ£o altera lÃ³gica, apenas registra.
    """
    logger.warning("â›” ===== VETO DE ENTRADA =====")

    for motivo in motivos:
        logger.warning(f"â€¢ {motivo}")

    logger.warning("ðŸ“Š CONTEXTO DA DECISÃƒO:")
    for chave, valor in contexto.items():
        logger.warning(f"   {chave}: {valor}")

    logger.warning("=================================")


def registrar_evento_log_events(level: str, logger_name: str, strategy: str, symbol: str, message: str):
    try:
        from banco_trade import db_trades

        conn = connect_operational_database()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO log_events (ts, level, logger, strategy, symbol, message) VALUES (%s, %s, %s, %s, %s, %s)",
            (datetime.utcnow().isoformat(), level, logger_name, strategy, symbol, message),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.warning(f"Falha ao registrar log_events: {e}")


# ============================================================
# ðŸŽ¯ FUNÃ‡ÃƒO UNIFICADA DE ORDENS (BUY/SELL) â€” VERSÃƒO PRO
# ============================================================

def executar_ordem_completa(
    session, symbol, side, qty,
    entry_price=None,
    strategy="breakout",
    entry_score=0,
    sentiment_score=0,
    order_type="Market",
    price=None,
    stop_loss=None,
    take_profit=None,
    trailing_offset=None,
    usar_ordem_limite=None,  # ðŸ†• Flag para ativar entrada por limite
    **kwargs
):
    """
    FunÃ§Ã£o unificada e blindada para criaÃ§Ã£o de ordens.
    
    ðŸ†• NOVO PARÃ‚METRO:
    - usar_ordem_limite: Se True, usa entrada profissional por ordem limite
                         com aguardo de execuÃ§Ã£o e timeout
    """

    # ---------------------------------------------------------
    # 1. Compatibilidade automÃ¡tica â€” Breakout envia price=
    # ---------------------------------------------------------
    if price is not None and entry_price is None:
        entry_price = price

    decision_id = kwargs.get("decision_id") or current_decision_id()
    if not decision_id:
        decision_id = create_decision(symbol=symbol, timeframe=kwargs.get("timeframe") or os.getenv("INTERVAL"),
                                      strategy=strategy, context={"side": side, "entry_price": entry_price,
                                                                  "entry_score": entry_score})
    record_event(decision_id, "ORDER_INTENT_CREATED", "ORDER", component="compra1",
                 payload={"symbol": symbol, "side": side, "strategy": strategy})

    try:
        # Hard risk guard: never open new order if daily/weekly loss limits are hit.
        risk_state = evaluate_risk_limits(symbol=symbol)
        process_risk_transition(risk_state, symbol=symbol, source="compra1.executar_ordem_completa")
        if risk_state.get("blocked"):
            record_event(decision_id, "RISK_BLOCKED", "RISK", status="BLOCKED", component="risk_guard",
                         reason_code=str(risk_state.get("reason") or "RISK_BLOCKED"))
            finalize_decision(decision_id, "BLOCKED", str(risk_state.get("reason") or "RISK_BLOCKED"))
            logging.warning(
                "ðŸš« Ordem bloqueada por RISK_GUARD | symbol=%s | side=%s | motivo=%s",
                symbol,
                side,
                risk_state.get("reason"),
            )
            write_kill_switch_flag(risk_state)
            return None
        record_event(decision_id, "RISK_ALLOWED", "RISK", status="ALLOWED", component="risk_guard")

        # Quality gates before order placement (spread + API ping latency)
        spread_info_pre = _get_spread_and_depth(session, symbol)
        api_ping_ms = _measure_api_ping_ms(session, symbol)
        max_spread_pct = _safe_env_float_local("MAX_ENTRY_SPREAD_PCT", 0.0)
        max_api_ping_ms = _safe_env_float_local("MAX_API_PING_MS", 0.0)

        if (
            max_spread_pct > 0
            and spread_info_pre.get("spread_pct") is not None
            and float(spread_info_pre["spread_pct"]) > max_spread_pct
        ):
            logging.warning(
                "ðŸš« Entrada bloqueada por spread alto | %s | spread=%.4f%% | max=%.4f%%",
                symbol,
                float(spread_info_pre["spread_pct"]),
                max_spread_pct,
            )
            register_critical_event(
                event_type="execution_guard_block",
                severity="WARNING",
                source="compra1.executar_ordem_completa",
                symbol=symbol,
                message=(
                    f"Entrada bloqueada por spread alto | spread={float(spread_info_pre['spread_pct']):.4f}% "
                    f"| max={max_spread_pct:.4f}%"
                ),
                details={"side": side, "spread_pct": float(spread_info_pre["spread_pct"]), "max_spread_pct": max_spread_pct},
            )
            return None

        if max_api_ping_ms > 0 and api_ping_ms is not None and float(api_ping_ms) > max_api_ping_ms:
            logging.warning(
                "ðŸš« Entrada bloqueada por latÃªncia API alta | %s | ping=%.1fms | max=%.1fms",
                symbol,
                float(api_ping_ms),
                max_api_ping_ms,
            )
            register_critical_event(
                event_type="execution_guard_block",
                severity="WARNING",
                source="compra1.executar_ordem_completa",
                symbol=symbol,
                message=(
                    f"Entrada bloqueada por latencia alta | ping={float(api_ping_ms):.1f}ms "
                    f"| max={max_api_ping_ms:.1f}ms"
                ),
                details={"side": side, "api_ping_ms": float(api_ping_ms), "max_api_ping_ms": max_api_ping_ms},
            )
            return None

        t_exec_start = time.perf_counter()

        # ---------------------------------------------------------
        # 2. Carrega DF ANTES de qualquer fallback
        # ---------------------------------------------------------
        df = get_market_data_bybit(symbol, session=session, limit=100)

        # ---------------------------------------------------------
        # 4. Calcula indicadores
        # ---------------------------------------------------------
        indicadores = calculate_indicators(df)


        # ---------------------------------------------------------
                # 4.1 Filtros de entrada (somente Early Trend)
        # ---------------------------------------------------------
        strategy_norm = str(strategy or "").strip().lower()
        try:
            if strategy_norm == "early_trend":
                ema_dist_max = float(os.getenv("EARLY_TREND_EMA12_DIST_MAX_MED20", "0") or 0.0)
                atr_spike_mult = float(os.getenv("EARLY_TREND_ATR_SPIKE_MULT", "0") or 0.0)
            else:
                ema_dist_max = 0.0
                atr_spike_mult = 0.0
        except Exception:
            ema_dist_max = 0.0
            atr_spike_mult = 0.0

        if ema_dist_max > 0 or atr_spike_mult > 0:
            try:
                ema12_s = indicadores["EMA_12"] if "EMA_12" in indicadores.columns else indicadores.get("EMA12")
                close_s = indicadores["close"] if "close" in indicadores.columns else None
                high_s = indicadores["high"] if "high" in indicadores.columns else None
                low_s = indicadores["low"] if "low" in indicadores.columns else None
                atr_s = indicadores["ATR"] if "ATR" in indicadores.columns else None

                if ema_dist_max > 0 and ema12_s is not None and close_s is not None and high_s is not None and low_s is not None:
                    close = float(close_s.iloc[-1])
                    ranges = (high_s - low_s).tail(20)
                    if len(ranges) > 0:
                        med20 = float(ranges.median())
                        if med20 > 0:
                            ema12 = float(ema12_s.iloc[-1])
                            dist_ratio = abs(close - ema12) / med20
                            if dist_ratio > ema_dist_max:
                                logging.warning(
                                    "ENTRY blocked: ema12_dist %.2f > max %.2f (med20=%.4f).",
                                    dist_ratio,
                                    ema_dist_max,
                                    med20,
                                )
                                return None

                if atr_spike_mult > 0 and atr_s is not None:
                    atr_cur = float(atr_s.iloc[-1])
                    atr_med = float(atr_s.tail(20).median()) if len(atr_s) >= 2 else atr_cur
                    if atr_med > 0 and (atr_cur / atr_med) > atr_spike_mult:
                        logging.warning(
                            "ENTRY blocked: ATR spike %.2f > max %.2f.",
                            (atr_cur / atr_med),
                            atr_spike_mult,
                        )
                        return None
            except Exception as e:
                logging.warning(f"Falha no filtro de entrada (Early Trend): {e}")

        # ---------------------------------------------------------
# 5. DetecÃ§Ã£o de contexto + Comandante IA
        # ---------------------------------------------------------
        contexto_mercado = detectar_contexto_mercado(df)
        tipo_stop_comandante = solicitar_tipo_stop_comandante(
            contexto_mercado,
            indicadores_para_dict(indicadores)
        )
        logging.info(f"ðŸ§­ Contexto: {contexto_mercado} | Stop sugerido: {tipo_stop_comandante}")

        # ============================================================
        # ðŸ”’ NORMALIZAÃ‡ÃƒO ÃšNICA E SOBERANA DO ORDER TYPE
        # ============================================================
        order_type_normalizado = normalizar_order_type(order_type)

        if order_type_normalizado is None:
            logging.error("ðŸš« Ordem abortada por order_type invÃ¡lido.")
            return None

        # ============================================================
        # ðŸ”’ PREÃ‡O SOBERANO PARA LIMIT
        # ============================================================
        if order_type_normalizado == "Limit":
            entry_price = price or entry_price
            if not entry_price or entry_price <= 0:
                logging.error("âŒ LIMIT sem preÃ§o vÃ¡lido. Abortando trade.")
                return None

        side_norm = (side or "").strip().lower()
        if side_norm not in ("buy", "sell"):
            motivo = f"side_ausente_ou_invalido: {side}"
            logging.error(f"ðŸš« {motivo}")
            registrar_evento_log_events("WARNING", "compra1", str(strategy or "unknown"), str(symbol or "UNKNOWN"), motivo)
            return None

        # ---------------------------------------------------------
        # 8ï¸âƒ£ Montagem FINAL dos parÃ¢metros da ordem
        # ---------------------------------------------------------
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side_norm.capitalize(),
            "orderType": order_type_normalizado,
            "qty": f"{qty:.6f}",
            "timeInForce": "GTC"
        }

        if order_type_normalizado == "Limit":
            params["price"] = f"{entry_price:.4f}"

        # ============================================================
        # ðŸ”§ Ajuste de QTY (min + step)
        # ============================================================
        try:
            qty_float = float(qty)
        except Exception:
            logging.error(f"âŒ qty invÃ¡lido: {qty}")
            return None

        min_qty = get_minimum_qty(symbol)
        if qty_float < min_qty:
            qty_float = min_qty

        step = get_step_size(symbol)
        qty_float = qty_float - (qty_float % step)
        qty_float = max(qty_float, min_qty)

        params["qty"] = f"{qty_float:.6f}"

        # Guardiao macro direcional: camada final anti-bypass.
        macro_regime, macro_fonte = inferir_direcao_macro(indicators=indicadores, market_data=df)
        confidence_score = kwargs.get("confidence_score", entry_score)
        permitido_guardiao, motivo_guardiao, fator_risco = permitir_operacao(
            side_norm,
            macro_regime,
            strategy,
            confidence_score=confidence_score,
        )
        tendencia_macro = kwargs.get("tendencia_macro") or kwargs.get("tendencia") or macro_regime.lower()
        permitido_macro, motivo_macro = validar_bloqueio_direcional(side_norm, tendencia_macro)
        permitido = permitido_guardiao and permitido_macro
        motivo = motivo_guardiao if not permitido_guardiao else motivo_macro

        if not permitido:
            contexto = {
                "side_solicitado": side_norm,
                "tendencia_macro": tendencia_macro,
                "macro_regime": macro_regime,
                "macro_fonte": macro_fonte,
                "estrategia": kwargs.get("strategy"),
                "score": kwargs.get("entry_score"),
                "regime": kwargs.get("regime"),
            }

            log_veto_entrada(contexto=contexto, motivos=[motivo])
            registrar_evento_log_events(
                "WARNING",
                "compra1",
                str(strategy or "unknown"),
                str(symbol or "UNKNOWN"),
                f"veto_direcional side={side_norm} macro={macro_regime} fonte={macro_fonte} motivo={motivo}",
            )
            return None

        if fator_risco < 1.0:
            qty_float = max(min_qty, qty_float * fator_risco)
            qty_float = qty_float - (qty_float % step)
            qty_float = max(qty_float, min_qty)
            params["qty"] = f"{qty_float:.6f}"

        trade_quality_lot_factor = kwargs.get("trade_quality_lot_factor")
        trade_quality_probabilidade = kwargs.get("trade_quality_probabilidade")
        try:
            trade_quality_lot_factor = float(trade_quality_lot_factor)
        except Exception:
            trade_quality_lot_factor = None
        qty_trade_quality = None
        if trade_quality_lot_factor is not None:
            qty_trade_quality = qty_float
            try:
                qty_trade_quality = ajustar_lote_por_probabilidade(qty_float, trade_quality_probabilidade)
            except Exception:
                qty_trade_quality = qty_float * max(min(trade_quality_lot_factor, 1.0), 0.0)
        if qty_trade_quality is not None and 0.0 < qty_trade_quality < qty_float:
            qty_float = max(min_qty, qty_trade_quality)
            qty_float = qty_float - (qty_float % step)
            qty_float = max(qty_float, min_qty)
            params["qty"] = f"{qty_float:.6f}"
            logging.info(
                "🧪 Trade Quality ajustou lote | symbol=%s | prob=%s | qty=%.6f",
                symbol,
                trade_quality_probabilidade,
                qty_float,
            )
        regime_unificado = detectar_regime_unificado(
            macro_regime=macro_regime,
            regime=kwargs.get("regime"),
            contexto_mercado=kwargs.get("contexto_mercado"),
        )
        comandante_result = avaliar_decisao_comandante(
            {
                "decisao_base": permitido,
                "score_estrategia": entry_score,
                "regime": regime_unificado,
                "contexto_mercado": regime_unificado,
                "strategy": strategy,
                "trailing_base": trailing_offset,
                "sinais_tecnicos": {
                    "rsi": indicadores["RSI"].iloc[-1] if "RSI" in indicadores.columns else None,
                    "adx": indicadores["ADX"].iloc[-1] if "ADX" in indicadores.columns else None,
                    "atr": indicadores["ATR"].iloc[-1] if "ATR" in indicadores.columns else None,
                },
            }
        )
        record_event(decision_id, "COMMANDER_EVALUATED", "COMMANDER", component="commander",
                     status="ALLOWED" if comandante_result.get("autorizado") else "BLOCKED",
                     reason_code=str(comandante_result.get("motivo") or "COMMANDER_ALLOWED"),
                     payload={"strategy": strategy, "score": entry_score})

        if not comandante_result.get("autorizado"):
            motivo_comandante = comandante_result.get("motivo", "comandante_bloqueou")
            contexto = {
                "symbol": symbol,
                "side_solicitado": side_norm,
                "macro_regime": macro_regime,
                "macro_fonte": macro_fonte,
                "estrategia": strategy,
                "score": entry_score,
                "regime": regime_unificado,
            }
            log_veto_entrada(contexto=contexto, motivos=[motivo_comandante])
            registrar_evento_log_events(
                "WARNING",
                "compra1",
                str(strategy or "unknown"),
                str(symbol or "UNKNOWN"),
                f"veto_comandante side={side_norm} regime={regime_unificado} motivo={motivo_comandante}",
            )
            logging.warning(
                "🚫 Entrada bloqueada pelo Comandante | symbol=%s | side=%s | motivo=%s",
                symbol,
                side_norm,
                motivo_comandante,
            )
            record_event(decision_id, "COMMANDER_BLOCKED", "COMMANDER", status="BLOCKED",
                         component="commander", reason_code=str(motivo_comandante))
            finalize_decision(decision_id, "BLOCKED", str(motivo_comandante))
            return None

        comandante_ajustes = comandante_result.get("ajustes") or {}
        fator_risco_comandante = float(comandante_ajustes.get("risco") or 1.0)
        if fator_risco_comandante != 1.0:
            qty_float = max(min_qty, qty_float * fator_risco_comandante)
            qty_float = qty_float - (qty_float % step)
            qty_float = max(qty_float, min_qty)
            params["qty"] = f"{qty_float:.6f}"
            logging.info(
                "🧭 Comandante ajustou risco da entrada | symbol=%s | fator=%.3f | qty=%.6f",
                symbol,
                fator_risco_comandante,
                qty_float,
            )
        if is_paper_trading_enabled():
            try:
                closes = []
                if "close" in indicadores.columns:
                    closes = [float(x) for x in indicadores["close"].tail(3).tolist()]
                bt_ret = kwargs.get("backtest_result")
                if bt_ret is not None:
                    try:
                        bt_ret = float(bt_ret)
                    except Exception:
                        bt_ret = None
                paper = register_paper_trade(
                    symbol=symbol,
                    side=side_norm,
                    qty=float(qty_float),
                    entry_price=float(entry_price or float(indicadores["close"].iloc[-1])),
                    strategy=str(strategy or "unknown"),
                    closes=closes,
                    backtest_return_pct=bt_ret,
                    source="compra1.executar_ordem_completa",
                )
                register_critical_event(
                    event_type="paper_trade_executed",
                    severity="INFO",
                    source="compra1.executar_ordem_completa",
                    symbol=symbol,
                    message=(
                        f"PAPER {side_norm.upper()} executado | order_id={paper.get('order_id')} | "
                        f"ret={paper.get('paper_return_pct', 0):.4f}%"
                    ),
                    details={
                        "strategy": strategy,
                        "qty": float(qty_float),
                        "entry_price": float(entry_price or 0.0),
                        "tracking_error_pct": paper.get("tracking_error_pct"),
                        "phase_next": (paper.get("phase_status") or {}).get("phase_next"),
                    },
                )
                register_strategy_audit(
                    event_type="paper_trade_executed",
                    symbol=symbol,
                    strategy=str(strategy or "unknown"),
                    side=side_norm,
                    params={
                        "qty": float(qty_float),
                        "entry_price": float(entry_price or 0.0),
                        "spread_pct": spread_info_pre.get("spread_pct"),
                        "api_ping_ms": api_ping_ms,
                    },
                    context={
                        "order_id": paper.get("order_id"),
                        "tracking_error_pct": paper.get("tracking_error_pct"),
                        "phase_next": (paper.get("phase_status") or {}).get("phase_next"),
                    },
                )
                logging.info(
                    "PAPER MODE ativo | symbol=%s | side=%s | order_id=%s | paper_ret=%.4f%%",
                    symbol,
                    side_norm,
                    paper.get("order_id"),
                    float(paper.get("paper_return_pct") or 0.0),
                )
                return {
                    "order_id": paper.get("order_id"),
                    "symbol": symbol,
                    "side": side_norm,
                    "entry_price": float(entry_price or 0.0),
                    "stop_loss": None,
                    "take_profit": None,
                    "trailing_offset": None,
                    "paper_mode": True,
                    "phase_status": paper.get("phase_status"),
                    "response_raw": {
                        "retCode": 0,
                        "retMsg": "PAPER_EXECUTED",
                        "result": {
                            "orderId": paper.get("order_id"),
                            "avgPrice": f"{float(entry_price or 0.0):.4f}",
                        },
                    },
                }
            except Exception as e:
                logging.error(f"Falha no PAPER MODE: {e}", exc_info=True)
                return None

        # ============================================================
        # ðŸ†• FLUXO DE ENTRADA POR ORDEM LIMITE (PROFISSIONAL)
        # ============================================================
        if usar_ordem_limite:
            logging.info("ðŸŽ¯ Ativando entrada profissional por ORDEM LIMITE")
            
            # Obter ATR para cÃ¡lculo do offset
            atr_bruto = indicadores.get("ATR", 0.0)
            atr_valor = float(atr_bruto.iloc[-1]) if isinstance(atr_bruto, pd.Series) else float(atr_bruto)
            
            if atr_valor <= 0:
                atr_valor = entry_price * 0.003  # Fallback: 0.3% do preÃ§o
            
            # Obter precisÃ£o do instrumento
            try:
                info = session.get_instruments_info(category="linear", symbol=symbol)
                item = info.get("result", {}).get("list", [{}])[0]
                tick_size = float(item.get("priceFilter", {}).get("tickSize", 0.01))
                qty_step = float(item.get("lotSizeFilter", {}).get("qtyStep", 0.001))
            except Exception:
                tick_size, qty_step = 0.01, 0.001
            
            # Executar entrada por limite com aguardo
            resultado_limite = executar_entrada_por_limite(
                api=session,
                symbol=symbol,
                side=side,
                qty=qty_float,
                entry_price=entry_price,
                atr=atr_valor,
                tick_size=tick_size,
                qty_step=qty_step,
                trade_id=kwargs.get("trade_id") or kwargs.get("trade_id_temp"),
                decision_timestamp=kwargs.get("timestamp_decisao_ia") or kwargs.get("decision_timestamp"),
                ttl_seconds=kwargs.get("ttl_seconds"),
            )
            
            if not resultado_limite:
                logging.warning("â° Entrada por limite CANCELADA (timeout ou falha)")
                return None
            
            # Usar preÃ§o e qty REAIS da execuÃ§Ã£o
            entry_real = resultado_limite["entry_price"]
            qty_executado = resultado_limite["qty"]
            order_id = resultado_limite["orderId"]
            
            logging.info(
                f"âœ… ENTRADA CONFIRMADA POR LIMITE | "
                f"Entry={entry_real:.2f} | Qty={qty_executado:.6f}"
            )
            
        else:
            # ============================================================
            # FLUXO ORIGINAL (MARKET)
            # ============================================================
            gate_result = executar_ordem_com_validacao(
                {
                    "trade_id": kwargs.get("trade_id") or kwargs.get("trade_id_temp"),
                    "symbol": symbol,
                    "side": side_norm,
                    "entry_price": entry_price,
                    "qty": qty_float,
                    "decision_timestamp": kwargs.get("timestamp_decisao_ia") or kwargs.get("decision_timestamp"),
                    "ttl_seconds": kwargs.get("ttl_seconds") or int(float(os.getenv("SIGNAL_TTL_SECONDS", "180"))),
                    "order_params": params,
                    "execution_fn": enviar_ordem_adaptativa,
                }
            )
            record_event(decision_id, "EXECUTION_GATE_EVALUATED", "EXECUTION_GATE",
                         status="ALLOWED" if gate_result.get("status") == "executado" else "BLOCKED",
                         component="execution_gate", reason_code=str(gate_result.get("motivo") or "EXECUTION_ALLOWED"))

            if gate_result.get("status") != "executado":
                logging.warning(
                    "ðŸš« ExecuÃ§Ã£o bloqueada pelo execution gate | symbol=%s | side=%s | motivo=%s",
                    symbol,
                    side_norm,
                    gate_result.get("motivo"),
                )
                record_event(decision_id, "EXECUTION_BLOCKED", "EXECUTION_GATE", status="BLOCKED",
                             component="execution_gate", reason_code=str(gate_result.get("motivo") or "EXECUTION_BLOCK"))
                finalize_decision(decision_id, "BLOCKED", str(gate_result.get("motivo") or "EXECUTION_BLOCK"))
                return None

            resp = (gate_result.get("detalhes") or {}).get("resposta")

            if not resp or resp.get("retCode") != 0:
                logging.error(f"âŒ Falha ao enviar ordem {side.upper()}: {resp}")
                record_event(decision_id, "ORDER_REJECTED", "ORDER", status="REJECTED", component="bybit",
                             reason_code="ORDER_REJECTED")
                finalize_decision(decision_id, "REJECTED", "ORDER_REJECTED")
                return None

            order_id = resp["result"]["orderId"]
            record_event(decision_id, "ORDER_ACCEPTED", "ORDER", status="ACCEPTED", component="bybit",
                         payload={"order_id": order_id})

            # ðŸ”§ Para "limit_confirmacao", pular SL/TP e retornar apenas order_id
            if order_type == "limit_confirmacao":
                logging.info(f"âœ… Ordem LIMIT enviada (sem SL/TP): {order_id}")
                return {
                    "orderId": order_id,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty_float
                }

            # ---------------------------------------------------------
            # ðŸ”‘ ENTRY REAL â€” ÃšNICO PREÃ‡O VÃLIDO
            # ---------------------------------------------------------
            entry_real = get_entry_price_fallback(symbol, order_id, None)

            if not entry_real or entry_real <= 0:
                logging.error("âŒ Falha ao obter entry_price REAL da Bybit.")
                return None
            
            qty_executado = qty_float

        exec_latency_ms = (time.perf_counter() - t_exec_start) * 1000.0
        slippage_abs = None
        slippage_pct = None
        try:
            if entry_price is not None and float(entry_price) > 0:
                _ref = float(entry_price)
                slippage_abs = float(entry_real) - _ref
                slippage_pct = (slippage_abs / _ref) * 100.0
        except Exception:
            pass

        max_exec_latency_ms = _safe_env_float_local("MAX_EXECUTION_LATENCY_MS", 0.0)
        if max_exec_latency_ms > 0 and exec_latency_ms > max_exec_latency_ms:
            logging.warning(
                "âš ï¸ LatÃªncia de execuÃ§Ã£o alta | %s | latency=%.1fms | max=%.1fms",
                symbol,
                exec_latency_ms,
                max_exec_latency_ms,
            )

        max_slippage_pct = _safe_env_float_local("MAX_ENTRY_SLIPPAGE_PCT", 0.0)
        if max_slippage_pct > 0 and slippage_pct is not None and abs(float(slippage_pct)) > max_slippage_pct:
            logging.warning(
                "âš ï¸ Slippage acima do limite | %s | slippage=%.4f%% | max=%.4f%%",
                symbol,
                float(slippage_pct),
                max_slippage_pct,
            )

        # ---------------------------------------------------------
        # 9ï¸âƒ£ Calcula SL / TP com preÃ§o REAL
        # ---------------------------------------------------------
        stop_loss, take_profit, tipo_stop, contexto = calcular_stop_take_adaptativo(
            df, entry_real, side.lower(), tipo_stop=tipo_stop_comandante
        )

        if stop_loss is None or take_profit is None:
            logging.error("âŒ SL/TP invÃ¡lidos apÃ³s entrada real.")
            return None

        # ---------------------------------------------------------
        # ðŸ”„ Trailing adaptativo
        # ---------------------------------------------------------
        atr_bruto = indicadores.get("ATR", 0.0)
        atr_valor = float(atr_bruto.iloc[-1]) if isinstance(atr_bruto, pd.Series) else float(atr_bruto)

        preco_atual = float(indicadores["close"].iloc[-1])
        atr_normalizado = atr_valor  # ATR jÃ¡ Ã© absoluto

        trailing_offset = calcular_trailing_adaptativo(
            atr_normalizado,
            tipo_stop,
            contexto
        )

        trailing_comandante = (comandante_ajustes if 'comandante_ajustes' in locals() else {}).get("trailing")
        if trailing_comandante not in (None, ""):
            try:
                trailing_offset = float(trailing_comandante)
            except Exception:
                pass

        # ---------------------------------------------------------
        # ðŸ” Aplica SL/TP UMA ÃšNICA VEZ
        # ---------------------------------------------------------
        # ---------------------------------------------------------
        # ðŸ’¾ Salva trade
        # ---------------------------------------------------------
        regime_auditoria = kwargs.get("regime") or macro_regime
        tendencia_auditoria = kwargs.get("tendencia") or kwargs.get("tendencia_macro") or macro_regime.lower()
        consenso_auditoria = kwargs.get("consenso") or kwargs.get("direcao_consenso") or kwargs.get("direcao")
        consenso_score_auditoria = kwargs.get("consenso_score")
        consenso_razao_auditoria = kwargs.get("consenso_razao") or kwargs.get("consenso_motivo") or motivo
        decisao_lstm_auditoria = kwargs.get("decisao_lstm") or kwargs.get("lado_lstm") or kwargs.get("lstm")

        persisted_trade_id = str(uuid.uuid4())
        salvar_trade_sqlite({
            "trade_id": persisted_trade_id,
            "decision_id": decision_id,
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "side": side.lower(),
            "qty": qty_executado,  # ðŸ†• Usa qty real executado
            "quantity": qty_executado,
            "order_id": order_id,
            "entry_price": entry_real,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": strategy,
            "entry_score": entry_score,
            "sentiment_score": sentiment_score,
            "tipo_stop": tipo_stop,
            "contexto": contexto,
            "regime": regime_auditoria,
            "tendencia": tendencia_auditoria,
            "consenso": consenso_auditoria,
            "consenso_score": consenso_score_auditoria,
            "consenso_razao": consenso_razao_auditoria,
            "decisao_lstm": decisao_lstm_auditoria,
            "spread": spread_info_pre.get("spread_abs"),
            "spread_pct": spread_info_pre.get("spread_pct"),
            "profundidade_livro": spread_info_pre.get("depth_qty_top5"),
            "api_ping_ms": api_ping_ms,
            "execution_latency_ms": exec_latency_ms,
            "slippage_abs": slippage_abs,
            "slippage_pct": slippage_pct,
        })
        # Persistir antes das protecoes pos-ordem: uma falha no SL/TP
        # nao pode deixar a ordem real sem rastreabilidade no banco.
        resp_tp_sl = aplicar_trading_stop_com_retry(
            session,
            symbol,
            stop_loss,
            take_profit
        )
        if not isinstance(resp_tp_sl, dict) or resp_tp_sl.get("retCode") != 0:
            # Nao registra a entrada como OPENED quando a protecao nao foi
            # confirmada. O worker de recuperacao continuara tentando o OCO,
            # mantendo este simbolo bloqueado ate a confirmacao da OKX.
            motivo_protecao = (resp_tp_sl or {}).get("retMsg", "SL/TP nao confirmados") if isinstance(resp_tp_sl, dict) else "SL/TP nao confirmados"
            logging.error("[PROTECTION_PENDING] %s entrada preenchida sem protecao confirmada: %s", symbol, motivo_protecao)
            finalize_decision(decision_id, "ERROR", "PROTECTION_NOT_CONFIRMED", "ProtectionPending")
            return None
        link_trade(decision_id, trade_id=persisted_trade_id, order_id=order_id, status="OPENED")
        finalize_decision(decision_id, "AUTHORIZED", "ORDER_ACCEPTED")
        register_strategy_audit(
            event_type="live_trade_executed",
            symbol=symbol,
            strategy=str(strategy or "unknown"),
            side=side_norm,
            params={
                "qty": qty_executado,
                "entry_price": entry_real,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_score": entry_score,
                "sentiment_score": sentiment_score,
                "spread_pct": spread_info_pre.get("spread_pct"),
                "api_ping_ms": api_ping_ms,
                "execution_latency_ms": exec_latency_ms,
                "slippage_pct": slippage_pct,
            },
            context={
                "order_id": order_id,
                "tipo_stop": tipo_stop,
                "contexto": contexto,
                "regime": regime_auditoria,
                "tendencia": tendencia_auditoria,
            },
        )

        # ---------------------------------------------------------
        # ðŸ“¢ Telegram
        # ---------------------------------------------------------
        try:
            send_telegram_message(
                f"ðŸš€ {side.upper()} EXECUTADA\n"
                f"{symbol}\n"
                f"Entrada: {entry_real:.4f}\n"
                f"Stop: {stop_loss:.4f}\n"
                f"Take: {take_profit:.4f}\n"
                f"Tipo: {tipo_stop} | {contexto}"
            )
        except Exception:
            pass

        # ---------------------------------------------------------
        # âœ… Retorno final
        # ---------------------------------------------------------
        return {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "entry_price": entry_real,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_offset": trailing_offset,
            "response_raw": resp_tp_sl
        }

    except Exception as e:
        logging.error(
            f"âŒ ERRO CRÃTICO em executar_ordem_completa ({symbol} | {side}): {e}",
            exc_info=True
        )
        finalize_decision(decision_id, "ERROR", "DECISION_ERROR", type(e).__name__)
        return None


# ============================================================
# ðŸ§© ALIASES DE COMPATIBILIDADE
# ============================================================

def create_order(symbol, side, qty, entry_price=None, **kwargs):
    # ðŸ†• Passa flag de ordem limite automaticamente
    usar_limite = kwargs.pop("usar_ordem_limite", USAR_ORDEM_LIMITE)
    return executar_ordem_completa(
        session, symbol, side.lower(), qty, entry_price, 
        usar_ordem_limite=usar_limite,
        **kwargs
    )

def create_buy_order(symbol, qty, entry_price=None, **kwargs):
    return executar_ordem_completa(session, symbol, "buy", qty, entry_price, **kwargs)

def create_sell_order(symbol, qty, entry_price=None, **kwargs):
    return executar_ordem_completa(session, symbol, "sell", qty, entry_price, **kwargs)



# âœ… FunÃ§Ã£o utilitÃ¡ria interna para SL/TP
def get_min_dist(atr):
    """
    Define distÃ¢ncia mÃ­nima aceitÃ¡vel entre entry e SL/TP.
    Usa metade do ATR como base, mas nunca menor que 30.
    """
    return max(atr * 0.5, 30)





ATR_MIN = float(os.getenv("ATR_MIN", 250))
RRR = float(os.getenv("RRR", 2.0))
TRAILING_ATR_MULT = float(os.getenv("TRAILING_ATR_MULT", 0.6))
EPS = 1e-6  # tolerÃ¢ncia para ponto flutuante


def _to_float(x):
    import pandas as pd
    if x is None:
        return None
    if isinstance(x, pd.Series):
        x = x.iloc[-1]
    return float(x)

# ----- Comparadores com tolerÃ¢ncia -----
def ge(a, b):  # a >= b (com tolerÃ¢ncia)
    return (a - b) >= -EPS

def le(a, b):  # a <= b (com tolerÃ¢ncia)
    return (b - a) >= -EPS

def gt(a, b):  # a > b (com tolerÃ¢ncia)
    return (a - b) > EPS

def lt(a, b):  # a < b (com tolerÃ¢ncia)
    return (b - a) > EPS

def distancia_valida(distancia, minimo):
    # considera vÃ¡lido quando for igual dentro da tolerÃ¢ncia
    return ge(distancia, minimo)






# --- ParÃ¢metros do .env ---
def get_atr_min_local():
    return float(os.getenv("ATR_MIN_LOCAL", 140))
def get_min_dist_atr_mult():
    return float(os.getenv("MIN_DIST_ATR_MULT", 0.6))
def get_min_dist_abs():
    return float(os.getenv("MIN_DIST_ABS", 20))
def get_max_dist_abs():
    return float(os.getenv("MAX_DIST_ABS", 150))
def get_taxa_margem_pct():
    return float(os.getenv("TAXA_MARGEM_PCT", 0.0015))
def get_taxa_margem_min():
    return float(os.getenv("TAXA_MARGEM_MIN", 2))
def get_rrr_vol_baixa():
    return float(os.getenv("RRR_VOL_BAIXA", 2.4))
def get_rrr_vol_alta():
    return float(os.getenv("RRR_VOL_ALTA", 1.6))
def get_vol_baixa_pct():
    return float(os.getenv("VOL_BAIXA_PCT", 0.002))
def get_vol_alta_pct():
    return float(os.getenv("VOL_ALTA_PCT", 0.008))
def get_trailing_atr_mult():
    return float(os.getenv("TRAILING_ATR_MULT", 0.8))
def get_trailing_dist_min_pct():
    return float(os.getenv("TRAILING_DIST_MIN_PCT", 0.4))





def sanitize_risk_params(entry_price, sl, tp, trailing, side, atr=None, ajustar=True):
    """
    Valida e (opcionalmente) ajusta os parÃ¢metros de risco.
    - entry_price: preÃ§o de entrada
    - sl: stop-loss sugerido
    - tp: take-profit sugerido
    - trailing: trailing sugerido
    - side: "buy" ou "sell"
    - atr: valor atual do ATR
    - ajustar: se True, corrige inconsistÃªncias; se False, apenas valida
    """
    import logging

    def clamp(x, lo, hi):
        return max(lo, min(hi, x))

    try:
        # --- NormalizaÃ§Ã£o ---
        entry_price = float(entry_price)
        sl = float(sl) if sl is not None else None
        tp = float(tp) if tp is not None else None
        trailing = float(trailing) if trailing is not None else None
        atr = float(atr) if atr is not None else None

        # === ATR mÃ­nimo adaptativo ===
        atr_fallback = entry_price * 0.003
        atr = max(atr or atr_fallback, get_atr_min_local())
        atr_pct = atr / entry_price

        # === DistÃ¢ncia mÃ­nima ===
        min_dist = clamp(get_min_dist_atr_mult() * atr, get_min_dist_abs(), get_max_dist_abs())

        # === Margem para taxas/spread ===
        taxa_margem = max(entry_price * get_taxa_margem_pct(), get_taxa_margem_min())

        # === RRR dinÃ¢mico ===
        if atr_pct <= get_vol_baixa_pct():
            RRR_eff = get_rrr_vol_baixa()
        elif atr_pct >= get_vol_alta_pct():
            RRR_eff = get_rrr_vol_alta()
        else:
            frac = (atr_pct - get_vol_baixa_pct()) / (get_vol_alta_pct() - get_vol_baixa_pct())
            RRR_eff = get_rrr_vol_baixa() + (get_rrr_vol_alta() - get_rrr_vol_baixa()) * frac


        # --- ValidaÃ§Ã£o e ajuste por lado ---
        if side.lower() == "buy":
            if not ajustar:
                if sl is None or tp is None or not (sl < entry_price and tp > entry_price):
                    return None, None, None
            else:
                # --- Ajusta Stop-Loss ---
                if sl is None or sl >= entry_price:
                    sl = round(entry_price - max(atr, min_dist), 2)

                # --- Ajusta Take-Profit ---
                if tp is None or tp <= entry_price:
                    tp = round(entry_price + max(RRR_eff * (entry_price - sl), min_dist + taxa_margem), 2)

                # --- Garante hierarquia correta ---
                if not (sl < entry_price < tp):
                    logging.warning("âŒ BUY: SL/TP invertidos, corrigindo orientaÃ§Ã£o...")
                    sl, tp = sorted([sl, tp])
                    if sl >= entry_price or tp <= entry_price:
                        tp = round(entry_price + (entry_price - sl) * RRR_eff, 2)

        elif side.lower() == "sell":
            if not ajustar:
                if sl is None or tp is None or not (sl > entry_price and tp < entry_price):
                    return None, None, None
            else:
                # --- Ajusta Stop-Loss ---
                if sl is None or sl <= entry_price:
                    sl = round(entry_price + max(atr, min_dist), 2)

                # --- Ajusta Take-Profit ---
                if tp is None or tp >= entry_price:
                    tp = round(entry_price - max(RRR_eff * (sl - entry_price), min_dist + taxa_margem), 2)

                # --- Garante hierarquia correta ---
                if not (tp < entry_price < sl):
                    logging.warning("âŒ SELL: SL/TP invertidos, corrigindo orientaÃ§Ã£o...")
                    sl, tp = sorted([sl, tp], reverse=True)
                    if sl <= entry_price or tp >= entry_price:
                        tp = round(entry_price - (sl - entry_price) * RRR_eff, 2)


        # === Checagem final de largura ===
        dist_total = abs(tp - sl)
        if dist_total < (min_dist + taxa_margem):
            logging.warning(f"âŒ DistÃ¢ncia {dist_total:.2f} < mÃ­nimo {(min_dist + taxa_margem):.2f}. Abortando.")
            return None, None, None

        # === Trailing Stop adaptativo ===
        if trailing is None or trailing <= 0:
            trailing_calc = max(get_trailing_atr_mult() * atr,
                                get_trailing_dist_min_pct() * dist_total)
            trailing = round(trailing_calc, 2)
        else:
            trailing = round(trailing, 2)

        logging.info(
            f"[SANITIZE] Side={side.upper()} | Entry={entry_price:.2f} | "
            f"SL={sl:.2f} | TP={tp:.2f} | TS={trailing:.2f} | "
            f"ATR={atr:.2f} | MinDist={min_dist:.2f} | RRR_eff={RRR_eff:.2f}"
        )

        return sl, tp, trailing

    except Exception as e:
        logging.error(f"âŒ Erro ao sanitizar SL/TP: {e}", exc_info=True)
        return None, None, None



    



# FunÃ§Ã£o auxiliar para avaliar distÃ¢ncia entre preÃ§o de mercado e ordem limite
def avaliar_se_mantem_ordem_limite(preco_mercado, preco_ordem, limiar_pct=0.001):
    """
    Verifica se a diferenÃ§a percentual entre o preÃ§o da ordem e o preÃ§o atual do mercado
    ainda estÃ¡ dentro de um limiar aceitÃ¡vel (padrÃ£o: 0.1%).
    """
    try:
        delta_pct = abs(preco_mercado - preco_ordem) / preco_ordem
        return delta_pct <= limiar_pct
    except Exception as e:
        logging.error(f"âŒ Erro ao avaliar distÃ¢ncia entre preÃ§os: {e}")
        return False




def _to_float_tail(x, default=None):
    import pandas as pd
    try:
        if x is None:
            return default
        if isinstance(x, pd.Series):
            return float(x.iloc[-1])
        return float(x)
    except Exception:
        return default




def executar_ordem_dinamica(
    acao, symbol, qty, entry_price, entry_score=0, sentiment_score=0,
    strategy="breakout", stop_loss=None, take_profit=None,
    trailing_stop=None, order_type="Market"
):
    """
    Executa uma ordem com SL, TP e trailing definidos, processa ajustes da IA
    e faz monitoramento atÃ© o encerramento.
    """

    # ===============================================================
    # 0) NormalizaÃ§Ã£o do lado da operaÃ§Ã£o (SEMPRE A VERDADE FINAL)
    # ===============================================================
    acao = acao.lower()                   # "buy" ou "sell"
    side = acao.capitalize()              # "Buy" ou "Sell"
    side_str = "buy" if acao == "buy" else "sell"

    # ===============================================================
    # 1) SanitizaÃ§Ã£o INICIAL + indicadores
    # ===============================================================
    df = get_market_data_bybit(symbol, session=session, limit=100)
    indicadores = calculate_indicators(df)

    macro_regime, macro_fonte = inferir_direcao_macro(indicators=indicadores, market_data=df)
    confidence_score = kwargs.get("confidence_score", entry_score)
    permitido_guardiao, motivo_guardiao, fator_risco = permitir_operacao(
        side_str,
        macro_regime,
        strategy,
        confidence_score=confidence_score,
    )
    if not permitido_guardiao:
        log_veto_entrada(
            contexto={
                "symbol": symbol,
                "side_solicitado": side_str,
                "macro_regime": macro_regime,
                "macro_fonte": macro_fonte,
                "estrategia": strategy,
                "entry_score": entry_score,
            },
            motivos=[motivo_guardiao],
        )
        registrar_evento_log_events(
            "WARNING",
            "compra1",
            str(strategy or "unknown"),
            str(symbol or "UNKNOWN"),
            f"veto_direcional side={side_str} macro={macro_regime} fonte={macro_fonte} motivo={motivo_guardiao}",
        )
        return {
            "status": "vetado_guardiao_direcional",
            "motivo": motivo_guardiao,
            "macro_regime": macro_regime,
            "symbol": symbol,
            "acao": acao,
        }

    if fator_risco < 1.0:
        qty = max(0.0, float(qty) * fator_risco)
        logging.info(f"ðŸ›¡ï¸ Guardiao direcional reduziu o lote por risco contra-tendÃªncia: fator={fator_risco:.2f}")

    # ===============================================================
    # 2) IA (Comandante) decide o tipo de stop ideal
    # ===============================================================
    contexto_mercado = detectar_contexto_mercado(df)
    tipo_stop_comandante = solicitar_tipo_stop_comandante(
        contexto_mercado,
        indicadores_para_dict(indicadores)
    )

    logging.info(
        f"ðŸ§  Contexto do mercado: {contexto_mercado} | Stop sugerido: {tipo_stop_comandante}"
    )

    # ===============================================================
    # 3) CÃ¡lculo correto do SL/TP considerando o LADO REAL da operaÃ§Ã£o
    # ===============================================================
    stop_loss, take_profit, tipo_stop, contexto = calcular_stop_take_adaptativo(
        df,
        entry_price,
        side_str,                   # <===== ponto crÃ­tico corrigido
        tipo_stop=tipo_stop_comandante
    )

    # ===============================================================
    # 4) ValidaÃ§Ã£o anti-erro â€” garante que o TP nunca venha invertido
    # ===============================================================
    if side_str == "buy":
        if take_profit <= entry_price:
            logging.warning("âš ï¸ TP invÃ¡lido para BUY detectado â€” corrigindo automaticamente.")
            # A lÃ³gica abaixo corrige mantendo o tamanho do alvo proporcional
            take_profit = entry_price + abs(take_profit - stop_loss)

    else:  # SELL
        if take_profit >= entry_price:
            logging.warning("âš ï¸ TP invÃ¡lido para SELL detectado â€” corrigindo automaticamente.")
            take_profit = entry_price - abs(take_profit - stop_loss)

    # ===============================================================
    # 5) Trailing adaptativo com ATR real
    # ===============================================================
    atr_valor = indicadores.get("ATR", 100)
    atr = float(atr_valor.iloc[-1] if isinstance(atr_valor, pd.Series) else atr_valor)

    trailing_stop = calcular_trailing_adaptativo(
        atr,
        tipo_stop,
        contexto
    )

    # ===============================================================
    # 6) LOG FINAL DO BLOCO DE RISCO (SL/TP/TS)
    # ===============================================================
    logging.info(
        f"ðŸ” SL: {stop_loss} | TP: {take_profit} | Trailing: {trailing_stop} | "
        f"EstratÃ©gia: {strategy} | Entry Score: {entry_score} | Sentimento: {sentiment_score}"
    )


    
    # ===============================================================
    # 2) Envio SEGURO da ordem inicial com SL/TP/Trailing
    # ===============================================================

    # ðŸ”§ Para limit_confirmacao, nÃ£o enviar SL/TP ainda
    sl_envio = stop_loss if order_type != "limit_confirmacao" else None
    tp_envio = take_profit if order_type != "limit_confirmacao" else None

    try:
        response = create_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            price=entry_price,
            stop_loss=sl_envio,
            take_profit=tp_envio,
            trailing_offset=None
        )
    except Exception as e:
        logging.error(f"âŒ EXCEÃ‡ÃƒO ao criar a ordem na Bybit: {e}")
        return {
            "status": "erro_envio",
            "motivo": "excecao_create_order",
            "erro": str(e),
            "symbol": symbol,
            "acao": acao
        }

    # ===============================================================
    # 2.1) ValidaÃ§Ã£o estrutural: a Bybit pode responder SEM orderId!
    # ===============================================================
    if response is None:
        logging.error("âŒ A Bybit retornou None â€” ordem nÃ£o criada.")
        return {
            "status": "erro_envio",
            "motivo": "response_none",
            "symbol": symbol,
            "acao": acao
        }

    if response.get("retCode") not in (0, "0", None):
        logging.error(f"âŒ Bybit rejeitou a ordem. CÃ³digo: {response.get('retCode')} | Msg: {response.get('retMsg')}")
        return {
            "status": "erro_envio",
            "motivo": "bybit_rejeitou",
            "codigo": response.get("retCode"),
            "mensagem": response.get("retMsg"),
            "symbol": symbol,
            "acao": acao
        }

    # Agora sim: ORDEM VÃLIDA. Ã‰ SEGURO ACESSAR ID E PREÃ‡O.
    order_id = (
        response.get("orderId")
        or (response.get("result") or {}).get("orderId")
        or f"simulado-{int(time.time())}"
    )

    avg_price_str = (
        response.get("avgPrice")
        or (response.get("result") or {}).get("avgPrice")
        or None
    )





    # sÃ³ prossegue se a ordem foi realmente criada
    # â³ Aguardar execuÃ§Ã£o por 30s
    ordem_executada = esperar_ordem_executada(session, symbol, order_id, timeout=30)

    # Se a LIMIT nÃ£o executou â†’ cancelar para nÃ£o ficar pendurada sem SL/TP
    if order_type.lower() == "limit" and not ordem_executada:
        logging.warning(f"ðŸª“ Ordem LIMIT {order_id} nÃ£o executou â€” cancelando automaticamente.")

        try:
            cancel_order(order_id, symbol)
            logging.info(f"âœ… Ordem LIMIT cancelada com sucesso: {order_id}")
        except Exception as e:
            logging.error(f"âŒ Falha ao cancelar LIMIT pendurada {order_id}: {e}")

        # Retorna um status claro sem afetar o restante do sistema
        return {
            "status": "limit_cancelada",
            "order_id": order_id,
            "symbol": symbol,
            "motivo": "nao_executou"
        }

    # ============================================================
    # ðŸ“Œ SE NÃƒO FOR LIMIT â†’ FLUXO NORMAL DE MARKET
    # ============================================================

    if order_type.lower() != "limit" and not ordem_executada:

        logging.warning("â³ Timeout: Ordem nÃ£o executada apÃ³s 30s.")

        preco_mercado_atual = obter_preco_mercado(session, symbol)
        preco_ordem = float(response.get("price") or entry_price or 0.0)

        # ðŸ” Verifica se temos dados vÃ¡lidos
        if preco_mercado_atual and preco_ordem > 0:

            # ðŸ“‰ ATR jÃ¡ seguro via patch 1
            atr_calc = _to_float_tail(indicadores.get("ATR"), default=None)
            atr_param = _to_float_tail(atr, default=entry_price*0.005)  # ajuste o fallback conforme seu padrÃ£o!
            atr_value = atr_calc if atr_calc and atr_calc > 0 else atr_param
            if not atr_value or atr_value <= 0:
                atr_value = max(preco_ordem * 0.005, 10)

            # ðŸ’¡ Limiar com base em volatilidade (25% do ATR)
            limiar_pct = max(0.001, (atr_value / preco_ordem) * 0.25)
            desvio_rel = abs(preco_mercado_atual - preco_ordem) / preco_ordem

            # ðŸ” MantÃ©m se ainda dentro do limiar
            if desvio_rel <= limiar_pct:
                logging.info("â³ PreÃ§o ainda prÃ³ximo da ordem limite. Estendendo tempo de espera (+20s)...")
                ordem_executada = esperar_ordem_executada(session, symbol, order_id, timeout=20)
            else:
                logging.info("ðŸª“ PreÃ§o afastou alÃ©m do limiar. Cancelando ordem pendente.")
                try:
                    cancel_order(order_id, symbol)
                except Exception as e:
                    logging.warning(f"Falha ao cancelar ordem {order_id}: {e}")
                return {"status": "cancelada_por_afastamento", "orderId": order_id}



            # Nova tentativa de aguardar execuÃ§Ã£o por mais 20 segundos
            ordem_executada = esperar_ordem_executada(session, symbol, order_id, timeout=20)

            if not ordem_executada:
                logging.warning("âŒ Ordem ainda nÃ£o executada apÃ³s tempo extra. Cancelando gestÃ£o.")
                return response
        else:
            logging.warning("âŒ PreÃ§o se afastou da ordem limite. Cancelando gestÃ£o.")
            return response

    # âœ… Tratamento seguro de avgPrice
    avg_price_str = (
        response.get("avgPrice")
        or (response.get("result") or {}).get("avgPrice")
        or None
    )
    if avg_price_str:
        try:
            preco_execucao = float(avg_price_str)
        except ValueError:
            preco_execucao = entry_price
    else:
        preco_execucao = entry_price


    if preco_execucao <= 0:
        logging.warning("âš ï¸ PreÃ§o de execuÃ§Ã£o invÃ¡lido. Usando preÃ§o de entrada.")
        preco_execucao = entry_price

    # âœ… Converte indicadores para float
    rsi_val = indicadores.get("RSI", 50.0)
    rsi_atual = float(rsi_val.iloc[-1] if isinstance(rsi_val, pd.Series) else rsi_val)

    atr_valor = indicadores.get("ATR", 100)
    atr = float(atr_valor.iloc[-1] if isinstance(atr_valor, pd.Series) else atr_valor)


    # âœ… Evita None nos parÃ¢metros
    stop_loss = float(stop_loss or 0.0)
    take_profit = float(take_profit or 0.0)
    trailing_stop = float(trailing_stop or 0.0)

    mensagem = (
        f"ðŸš€ ORDEM EXECUTADA ({acao.upper()})\n"
        f"ID: {order_id}\n"
        f"Ativo: {symbol}\n"
        f"Qtd: {qty}\n"
        f"Entrada: {preco_execucao:.2f}\n"
        f"Stop-Loss: {stop_loss:.2f}\n"
        f"Take-Profit: {take_profit:.2f}\n"
        f"Trailing: {trailing_stop:.2f}\n"
        f"EstratÃ©gia: {strategy}\n"
        f"Score entrada: {entry_score}\n"
        f"Score sentimento: {sentiment_score}\n"
        f"HorÃ¡rio: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    )
    panel_url = os.getenv("GENESIS_PANEL_URL", "https://genesis-panel.onrender.com").rstrip("/")
    mensagem += f"\nVer posição ao vivo: {panel_url}/positions"
    try:
        send_telegram_message(mensagem)
    except Exception as e:
        logging.warning(f"Falha ao enviar mensagem Telegram: {e}")


    # 3) IA pÃ³s-ordem: ajustes dinÃ¢micos
    tendencia_macro = macro_regime.lower()
    sentimento = "neutro"

    # âœ… Verifica se o preÃ§o de execuÃ§Ã£o Ã© vÃ¡lido (para evitar entry_price=0)
    if preco_execucao <= 0:
        logging.warning("âš ï¸ PreÃ§o de execuÃ§Ã£o indefinido. Tentando recuperar via Bybit antes de enviar para IA...")
        for tentativa in range(5):
            try:
                pos = session.get_positions(category="linear", symbol=symbol)
                if pos.get("retCode") == 0 and pos.get("result", {}).get("list"):
                    lista = pos["result"]["list"]
                    for p in lista:
                        if float(p.get("size", 0)) > 0:
                            preco_execucao = float(p.get("entryPrice", 0))
                            if preco_execucao > 0:
                                logging.info(f"âœ… PreÃ§o recuperado da posiÃ§Ã£o: {preco_execucao:.2f} (tentativa {tentativa+1})")
                                break
                if preco_execucao > 0:
                    break
                else:
                    logging.warning(f"â³ PosiÃ§Ã£o ainda nÃ£o disponÃ­vel ({tentativa+1}/5). Aguardando 2s...")
                    time.sleep(2)
            except Exception as e:
                logging.warning(f"âš ï¸ Tentativa {tentativa+1} falhou: {e}")
                time.sleep(2)

        if preco_execucao <= 0:
            logging.error("âŒ Falha ao recuperar entry_price. Abortando chamada Ã  IA.")
        else:
            logging.info("âœ… PreÃ§o confirmado. Prosseguindo para IA...")

    manage_payload = {
        "trade_id": order_id,
        "symbol": symbol,
        "entry_price": preco_execucao,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "trailing_offset": trailing_stop,
        "rsi": rsi_atual,
        "atr": atr,
        "tendencia_macro": tendencia_macro,
        "sentimento": sentimento,
        "side": side.lower(),
    }

    # âœ… Envia somente se houver preÃ§o de entrada vÃ¡lido
    if preco_execucao > 0:
        try:
            resp = requests.post(f"{API_IA_URL}/manage_trade", json=manage_payload, timeout=10)
            if resp.status_code != 200:
                logging.error(f"âŒ /manage_trade retornou {resp.status_code}: {resp.text}")
                encerrar = False
                novos_parametros = {}
            else:
                logging.info("âœ… /manage_trade processado com sucesso.")
                data = resp.json()
                if data is None:
                    logging.error("âŒ /manage_trade retornou JSON None.")
                    encerrar = False
                    novos_parametros = {}
                else:
                    encerrar = data.get("encerrar", False)
                    novos_parametros = data.get("novos_parametros", {})
        except Exception as e:
            logging.error(f"âŒ Erro ao chamar /manage_trade: {e}", exc_info=True)
            encerrar = False
            novos_parametros = {}
    else:
        logging.warning("ðŸš« /manage_trade nÃ£o chamado (entry_price indefinido).")
        encerrar = False
        novos_parametros = {}



    # 4) SanitizaÃ§Ã£o FINAL apÃ³s IA
    stop = novos_parametros.get("stop_loss", stop_loss)
    take = novos_parametros.get("take_profit", take_profit)
    trailing = novos_parametros.get("trailing_stop", trailing_stop)

    # Primeiro, sanitize normalmente (filtra valores fora do padrÃ£o, NaN, invertidos, etc.)
    stop, take, trailing = sanitize_risk_params(
        preco_execucao,
        stop,
        take,
        trailing,
        side=acao,
        atr=atr
    )

    # Agora calcula o padrÃ£o adaptativo robusto
    sl_min, tp_min, _, _ = calcular_stop_take_adaptativo(df, preco_execucao, side=acao)

    # Troca os valores para o mais conservador/distante
    if acao == "buy":
        stop = min(stop, sl_min)    # O stop deve ser SEMPRE menor (mais longe da entrada, mais seguro)
        take = max(take, tp_min)    # O take deve ser SEMPRE maior (mais longe da entrada, mais seguro)
    else:
        stop = max(stop, sl_min)    # O stop deve ser SEMPRE maior para venda
        take = min(take, tp_min)    # O take deve ser SEMPRE menor para venda


    # 5) Aplica novos parÃ¢metros se nÃ£o encerrar
    if not encerrar:
        try:
            logging.info("ðŸ› ï¸ Reajustando SL/TP/TS pela IA...")
            session.set_trading_stop(
                category="linear",
                symbol=symbol,
                stopLoss=str(stop),
                takeProfit=str(take),
                tpTriggerBy="LastPrice",
                slTriggerBy="LastPrice",
                triggerDirection=1 if acao == "buy" else 2
            )
            logging.info("âœ… SL/TP aplicados com sucesso. Trailing serÃ¡ ativado apenas no monitoramento dinÃ¢mico.")
        except Exception as e:
            logging.warning(f"âš ï¸ Falha ao aplicar SL/TP apÃ³s ajustes da IA: {e}")

    # 5.5) Flag de operaÃ§Ã£o ativa
    with open("operacao_ativa.flag", "w") as f:
        f.write("ATIVA")

    # 6) Inicia monitoramento
    risk_parameters = {
        "stop_loss": stop,
        "take_profit": take,
        "trailing_stop": trailing,
        "regime": macro_regime,
        "tendencia": tendencia_macro,
        "timestamp_decisao_ia": datetime.utcnow().isoformat(),
    }

    # âœ… Inclui o seed do trailing (valor calculado no create_order)
    risk_parameters["trailing_seed"] = response.get("trailingOffsetSeed", None)
    logging.info(f"ðŸ’¾ Trailing seed recebido da entrada: {risk_parameters['trailing_seed']}")

    monitorar_ordem_ate_encerrar(
        trade_id=order_id,
        symbol=symbol,
        entry_price=preco_execucao,
        position_size=qty,
        risk_parameters=risk_parameters,
        indicators={
            "RSI": rsi_atual,
            "ATR": atr,
            "score": entry_score,
            "estrategia": strategy
        },
        api=session,
        side=acao
    )

    return response







def calcular_qty_maxima(saldo_usdt, preco_entrada, alavancagem=2, margem_pct=0.05):
    capital_usado = saldo_usdt * margem_pct
    qty_max = (capital_usado * alavancagem) / preco_entrada
    return round(qty_max, 6)



DELTA_TIMESTAMP = obter_diferenca_timestamp()

def get_step_size(symbol=None):
    symbol = (symbol or os.getenv("ATIVOS") or "BTCUSDT").strip().upper()
    try:
        r = session.get_instruments_info(category="linear", symbol=symbol)
        info = r["result"]["list"][0]["lotSizeFilter"]["qtyStep"]
        step = float(info)
        logging.info(f"âœ… qtyStep {symbol}: {step}")
        return step
    except Exception as e:
        logging.error(f"âŒ get_step_size: {e}")
        return 0.001




def get_min_qty(symbol):
    try:
        info = session.get_instruments_info(category="linear", symbol=symbol)
        min_qty = float(info["result"]["list"][0]["lotSizeFilter"]["minOrderQty"])
        return min_qty
    except Exception as e:
        logging.warning(f"âš ï¸ Falha ao obter min_qty para {symbol}: {e}")
        return 0.001  # fallback conservador




def get_order_status(order_id):
    try:
        return session.get_order_list(category=CATEGORY, orderId=order_id)
    except Exception as e:
        logging.error(f"Erro ao consultar status da ordem: {e}")
        return None
    

def get_minimum_qty(symbol=None):
    symbol = (symbol or os.getenv("ATIVOS") or "BTCUSDT").strip().upper()
    try:
        r = session.get_instruments_info(category="linear", symbol=symbol)
        min_qty = float(r["result"]["list"][0]["lotSizeFilter"]["minOrderQty"])
        logging.info(f"âœ… minOrderQty {symbol}: {min_qty}")
        return min_qty
    except Exception as e:
        logging.error(f"âŒ get_minimum_qty: {e}")
        return 0.001



def ajustar_para_min_qty(symbol, qty):
    """
    Garante que a quantidade esteja acima do mÃ­nimo permitido pela Bybit.
    """
    min_qty = get_minimum_qty(symbol) or 0.001  # fallback seguro
    if qty < min_qty:
        logging.warning(f"âš ï¸ Quantidade ajustada de {qty} para mÃ­nimo permitido: {min_qty}")
        return min_qty
    return qty


def cancel_order(order_id, symbol): 
    try:
        return session.cancel_order(category=CATEGORY, symbol=symbol, orderId=order_id)
    except Exception as e:
        logging.error(f"Erro ao cancelar ordem {order_id} para {symbol}: {e}")
        return None


def list_open_orders():
    try:
        return session.get_order_list(category=CATEGORY)
    except Exception as e:
        logging.error(f"Erro ao listar ordens abertas: {e}")
        return None
    



# ====================================================
# ðŸ” Compatibilidade retroativa: funÃ§Ãµes executoras
# ====================================================
def executar_compra(symbol, df, resultados=None, trade_id_temp=None):
    """Compatibilidade: executa compra chamando create_buy_order."""
    entry_price = float(df["close"].iloc[-1])
    qty = 0.001  # ou calcule conforme lÃ³gica dinÃ¢mica
    return create_buy_order(symbol, qty, entry_price, strategy="breakout", trade_id_temp=trade_id_temp)

def executar_venda(symbol, df, resultados=None, trade_id_temp=None):
    """Compatibilidade: executa venda chamando create_sell_order."""
    entry_price = float(df["close"].iloc[-1])
    qty = 0.001  # ou calcule conforme saldo
    return create_sell_order(symbol, qty, entry_price, strategy="breakout", trade_id_temp=trade_id_temp)


# âœ… Alias de compatibilidade â€” mantÃ©m compatibilidade com versÃµes antigas
initialize_session = create_bybit_session
# Import lazy para evitar circularidade durante o boot do worker.
def monitorar_ordem_ate_encerrar(*args, **kwargs):
    from monitoramento import monitorar_ordem_ate_encerrar as _monitorar
    return _monitorar(*args, **kwargs)




