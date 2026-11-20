import streamlit as st
import psutil
import os
import subprocess
import time
import streamlit as st
# ===== REFRESH AUTOMÁTICO SEM LOOP =====

def st_autorefresh(interval=3000, key="autorefresh"):
    if f"{key}_last" not in st.session_state:
        st.session_state[f"{key}_last"] = time.time()
    now = time.time()
    if now - st.session_state[f"{key}_last"] >= interval / 1000:
        st.session_state[f"{key}_last"] = now
        st.experimental_rerun()

st_autorefresh(interval=3000, key="test")
st.write(f"🕒 {time.strftime('%H:%M:%S')}")

refresh_interval = 5  # segundos
st_autorefresh(interval=refresh_interval * 1000, key="datarefresh")

st.set_page_config(page_title="GENESES QUANT - Monitoramento de Status (Auto)", layout="wide")
st.title("GENESES QUANT - Monitoramento de Status (Auto)")

# ========== MONITORAMENTO ==========
st.header(f"🔄 Status em tempo real (a cada {refresh_interval}s)")
ativos = {
    "BTCUSDT": [("trading_bot_geneses.py", ".env-btc"), ("api_agente_ia:app", "8000")],
    "ETHUSDT": [("trading_bot_geneses.py", ".env-eth"), ("api_agente_ia:app", "8011")],
    "SOLUSDT": [("trading_bot_geneses.py", ".env-sol"), ("api_agente_ia:app", "8002")],
    "BNBUSDT": [("trading_bot_geneses.py", ".env-bnb"), ("api_agente_ia:app", "8003")],
    "XRPUSDT": [("trading_bot_geneses.py", ".env-xrp"), ("api_agente_ia:app", "8004")],
}
processos = []
for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        cmd = " ".join(proc.info.get('cmdline') or [])
        processos.append((cmd, proc.pid))
    except Exception:
        continue
for nome, requisitos in ativos.items():
    encontrado_todos = True
    for key, arg in requisitos:
        if not any((key in cmd and arg in cmd) for cmd, pid in processos):
            encontrado_todos = False
    if encontrado_todos:
        st.success(f"✅ {nome} está RODANDO (robô + IA)")
    else:
        st.warning(f"⛔ {nome} não está ativo ou faltando componente (robô ou IA)")
if any("lstm_service.py" in cmd for cmd, pid in processos):
    st.success("✅ LSTM está RODANDO")
else:
    st.warning("⛔ LSTM não está ativo.")

st.info(f"⏳ Atualizando status novamente em {refresh_interval} segundos...")

# ========== LOGS ==========
st.subheader("📄 Logs Recentes do Robô BTCUSDT")
LOG_PATH = r"C:\Users\emgepron\Documents\robo\logs\trading_bot_geneses.log"
if os.path.exists(LOG_PATH):
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        linhas = f.readlines()[-15:]
        st.text("".join(linhas))
else:
    st.info("Sem logs recentes encontrados.")
