import streamlit as st
import time
import pandas as pd
from estrategia_market_making import obter_preco_mercado, verificar_saldo, SYMBOL

st.set_page_config(page_title="Dashboard Market Making", layout="wide")
st.title("📈 Market Making Dashboard - BTCUSDT")

# Atualização em tempo real a cada X segundos
UPDATE_INTERVAL = 5

# Loop contínuo (pode ser controlado com botão)
placeholder = st.empty()

with placeholder.container():
    while True:
        try:
            preco = obter_preco_mercado()
            saldo = verificar_saldo()

            col1, col2 = st.columns(2)

            with col1:
                st.metric("💰 Preço de Mercado (BTC/USDT)", f"${preco:.2f}")

            with col2:
                st.metric("🪙 Saldo USDT", f"${saldo:.2f}")

            # Aguarda para atualizar
            time.sleep(UPDATE_INTERVAL)

        except Exception as e:
            st.error(f"Erro ao obter dados: {e}")
            break
