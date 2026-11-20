import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

# Carregar .env-btc para BTC
load_dotenv('.env-btc')

# Adicionar caminho do projeto
sys.path.append(r'c:\Users\emgepron\Documents\robo')

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Importar apenas o necessário
from compra1 import session, executar_ordem_dinamica
from utils.parse_env import get_ativos_env

def testar_ordem_limite_simples(symbol="BTCUSDT"):
    """
    Teste simples: Envia ordem LIMIT manual com parâmetros fixos.
    """
    print(f"🚀 Teste manual simples para {symbol}...")

    # Verificar se está em produção
    testnet = os.getenv("TESTNET", "false").lower() == "true"
    if testnet:
        print("⚠️ Sistema em TESTNET.")
    else:
        print("🔴 ATENÇÃO: PRODUÇÃO! Use qty mínima (0.001).")

    # Parâmetros fixos para teste (ajuste conforme ativo)
    side = "buy"
    preco_limit = 88300.0  # Exemplo para BTC ~88k; ajuste para o ativo atual
    qty = 0.001  # Mínima para evitar riscos

    print(f"📤 Ordem LIMIT: {side.upper()} {qty} @ {preco_limit}")

    # Confirmar
    confirm = input("🔴 Confirmar envio de ordem LIMIT real? (sim/não): ").strip().lower()
    if confirm != "sim":
        print("❌ Cancelado.")
        return

    # Enviar ordem
    resposta = executar_ordem_dinamica(
        acao=side,
        symbol=symbol,
        qty=qty,
        entry_price=preco_limit,
        entry_score=5,
        sentiment_score=0,
        strategy="teste_manual",
        order_type="limit_confirmacao"
    )

    if resposta and "orderId" in resposta:
        trade_id = resposta["orderId"]
        print(f"✅ Ordem enviada: {trade_id}")

        # Aguardar execução
        from utils.monitoramento_ordem import esperar_ordem_executada
        executada = esperar_ordem_executada(
            session, symbol, trade_id, timeout=60, extend_if_price_within=0.004, max_total_timeout=450,
            allow_market_fallback=False, validate_breakout=None, estrategia="breakout"  # Sem fallback para teste puro
        )

        if executada:
            print("🎉 Executada! SL/TP aplicados e monitoramento ativo.")
        else:
            print("⏳ Não executada dentro do timeout. Cancelada.")
    else:
        print("❌ Falha no envio.")

if __name__ == "__main__":
    symbol = get_ativos_env()[0] if get_ativos_env() else "BTCUSDT"
    testar_ordem_limite_simples(symbol)