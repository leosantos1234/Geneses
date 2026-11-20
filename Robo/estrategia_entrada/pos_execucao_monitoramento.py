# pos_execucao_monitoramento.py
import logging
import time
import requests

def preparar_trade_para_monitoramento(session, order_id, symbol, trade_id, strategy, qty, indicadores_df):
    """
    Realiza pós-processamento da ordem e inicia o monitoramento com IA.
    """

    try:
        # Aguarda breve momento para garantir que a ordem esteja refletida
        time.sleep(1)

        # Consulta status da ordem para obter preço de entrada real
        detalhes_ordem = session.get_order_list(category="linear", orderId=order_id)
        info = detalhes_ordem.get("result", {}).get("list", [])[0]

        entry_price = float(info.get("avgPrice") or info.get("triggerPrice") or 0)
        if entry_price == 0:
            logging.warning("⚠️ Preço de entrada ausente. Usando último fechamento.")
            entry_price = float(indicadores_df["close"].iloc[-1])

        # Extrai indicadores técnicos
        rsi = float(indicadores_df["RSI"].iloc[-1])
        atr = float(indicadores_df["ATR"].iloc[-1])
        score = indicadores_df["entry_score"].iloc[-1] if "entry_score" in indicadores_df.columns else 0
        sentimento = indicadores_df["sentiment_score"].iloc[-1] if "sentiment_score" in indicadores_df.columns else 0

        stop_loss = float(info.get("stopLoss") or 0)
        take_profit = float(info.get("takeProfit") or 0)
        trailing = float(info.get("trailingStop") or 0)

        payload = {
            "trade_id": trade_id,
            "symbol": symbol,
            "entry_price": entry_price,
            "position_size": qty,
            "risk_parameters": {
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "trailing_stop": trailing
            },
            "indicators": {
                "RSI": rsi,
                "ATR": atr,
                "entry_score": score,
                "sentiment_score": sentimento,
                "estrategia": strategy
            }
        }

        # Envia para a API de monitoramento
        response = requests.post("http://localhost:8000/manage_trade", json=payload, timeout=10)

        if response.status_code == 200:
            logging.info(f"✅ Monitoramento iniciado com sucesso para trade {trade_id}")
        else:
            logging.error(f"❌ Erro no monitoramento: {response.status_code} → {response.text}")

    except Exception as e:
        logging.error(f"❌ Falha no pós-execução do trade: {e}", exc_info=True)
