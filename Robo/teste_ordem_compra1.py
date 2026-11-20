import os
import time
import logging
from dotenv import load_dotenv
from compra1 import create_order, session, verificar_sessao_ativa, get_balance, obter_preco_mercado

# === 1️⃣ Carrega ambiente correto ===
load_dotenv('.env-btc')

# === 2️⃣ Configura logging ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

symbol = os.getenv("ATIVOS", "BTCUSDT").strip().upper()
side = "Buy"                     # ou "Sell" se quiser testar venda
qty = 0.001                      # mínimo permitido na Bybit (testnet)
order_type = "Market"

print("🔍 Iniciando teste de ordem PERPÉTUO Bybit...")

# === 3️⃣ Verifica sessão antes de prosseguir ===
if not verificar_sessao_ativa():
    print("⚠️ Sessão não estava ativa — recriada automaticamente.")
else:
    print("✅ Sessão Bybit ativa e funcional.")

# === 4️⃣ Consulta saldo ===
saldo = get_balance()
print(f"💰 Saldo atual detectado: {saldo} USDT")

# === 5️⃣ Obtém o preço atual de mercado (entry_price) ===
entry_price = obter_preco_mercado(session, symbol)
if not entry_price:
    raise RuntimeError("❌ Não foi possível obter o preço de mercado. Abortando teste.")

print(f"💹 Entry price detectado: {entry_price}")

# === 6️⃣ Define alavancagem (opcional) ===
try:
    resp_leverage = session.set_leverage(
        category="linear",
        symbol=symbol,
        buyLeverage=1,
        sellLeverage=1
    )
    print("🧮 Alavancagem definida:", resp_leverage)
except Exception as e:
    print("⚠️ Erro ao definir alavancagem (pode ignorar na testnet):", e)

# === 7️⃣ Cria a ordem real ===
try:
    print(f"📤 Enviando ordem: {side.upper()} {qty} {symbol} (entry={entry_price:.2f})...")
    response = create_order(
        symbol=symbol,
        side=side,
        qty=qty,
        entry_price=entry_price,  # 🔥 AQUI ESTÁ O PULO DO GATO
        order_type=order_type
    )

    # === 8️⃣ Valida retorno ===
    if response and isinstance(response, dict):
        print("\n✅ Ordem enviada com sucesso!")
        for k, v in response.items():
            print(f"   {k}: {v}")
    else:
        print("\n❌ Falha ao criar ordem. Resposta:", response)

except Exception as e:
    print("❌ Erro crítico ao criar ordem:", e)

# === 9️⃣ Aguarda logs ===
time.sleep(3)
print("✅ Teste finalizado. Verifique o log 'compra1.log' para detalhes.")
