import streamlit as st
import os
import time
import pandas as pd

st.set_page_config(page_title="Robô Fluxo Dashboard", layout="wide")

st.title("🤖 Dashboard Robô Fluxo")

# --- Parâmetros de ativos monitorados ---
ativos = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # Edite conforme necessário

# --- Status dos agentes (mock/fake; troque por leitura real se quiser) ---
def get_status_agente(ativo):
    # Você pode ler isso de um arquivo de status, banco ou API
    flag_file = f"operacao_ativa_{ativo}.flag"
    if os.path.exists(flag_file):
        return "Operando", "🟢"
    else:
        return "Parado", "🔴"

# --- Últimos trades (busca em SQLite ou CSV, adapte para seu formato real) ---
def get_ultimos_trades(ativo, n=5):
    path = f"trades_{ativo}.csv"
    if os.path.exists(path):
        df = pd.read_csv(path)
        return df.tail(n)
    return pd.DataFrame(columns=["timestamp", "symbol", "side", "qty", "entry_price", "exit_price", "profit_loss"])

# --- Logs recentes ---
def get_log_recent(ativo, linhas=20):
    log_path = f"{ativo.lower()}.log"
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-linhas:]
            return "".join(lines)
    return "Sem logs recentes..."

# --- Painel por ativo ---
cols = st.columns(len(ativos))
for idx, ativo in enumerate(ativos):
    with cols[idx]:
        st.subheader(f"{ativo}")

        status, emoji = get_status_agente(ativo)
        st.markdown(f"**Status:** {emoji} {status}")

        # Botões (exemplo, precisa conectar com backend se quiser controlar)
        col1, col2 = st.columns(2)
        with col1:
            if st.button(f"Iniciar {ativo}", key=f"start_{ativo}"):
                os.system(f"python robo_fluxo.py --ativo {ativo} &")
                st.success("Robô iniciado! (Mock)")
        with col2:
            if st.button(f"Parar {ativo}", key=f"stop_{ativo}"):
                flag = f"operacao_ativa_{ativo}.flag"
                if os.path.exists(flag): os.remove(flag)
                st.info("Robô parado! (Mock)")

        st.markdown("### Últimos Trades")
        df_trades = get_ultimos_trades(ativo)
        st.dataframe(df_trades, use_container_width=True)

        st.markdown("### Logs Recentes")
        st.code(get_log_recent(ativo), language="log")

st.markdown("---")
st.markdown("_Dashboard inicial. Personalize à vontade: gráficos, painel IA, LSTM, controle avançado..._")
