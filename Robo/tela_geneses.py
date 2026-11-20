import streamlit as st
import psutil
import os
import time
from dotenv import dotenv_values, set_key

# =====================================================
# 🧠 CONFIGURAÇÃO INICIAL
# =====================================================
st.set_page_config(page_title="GENESES QUANT - Painel de Monitoramento", layout="wide")

# ===== REFRESH AUTOMÁTICO =====
def st_autorefresh(interval=3000, key="autorefresh"):
    if f"{key}_last" not in st.session_state:
        st.session_state[f"{key}_last"] = time.time()
    now = time.time()
    if now - st.session_state[f"{key}_last"] >= interval / 1000:
        st.session_state[f"{key}_last"] = now
        st.rerun()

# =====================================================
# 🚀 INTERVALO DE ATUALIZAÇÃO
# =====================================================
refresh_interval = 5  # segundos
st_autorefresh(interval=refresh_interval * 1000, key="datarefresh")

st.title("⚡ GENESES QUANT - Painel de Monitoramento")

abas = st.tabs(["📊 Status em Tempo Real", "🧾 Logs Recentes", "⚙️ Configurações (.env)"])

# =====================================================
# 📊 ABA 1 — STATUS EM TEMPO REAL
# =====================================================
with abas[0]:
    st.header(f"🔄 Status dos Robôs e IA (atualiza a cada {refresh_interval}s)")

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

    col1, col2 = st.columns(2)
    for nome, requisitos in ativos.items():
        encontrado_todos = True
        for key, arg in requisitos:
            if not any((key in cmd and arg in cmd) for cmd, pid in processos):
                encontrado_todos = False
        if encontrado_todos:
            col1.success(f"✅ {nome} está RODANDO (robô + IA)")
        else:
            col2.warning(f"⛔ {nome} não está ativo ou faltando componente (robô ou IA)")

    if any("lstm_service.py" in cmd for cmd, pid in processos):
        st.success("✅ LSTM central está ativo")
    else:
        st.warning("⛔ LSTM central não está ativo.")

    st.info(f"⏳ Atualização automática a cada {refresh_interval}s.")


# =====================================================
# 📄 ABA 2 — LOGS RECENTES
# =====================================================
with abas[1]:
    st.header("📜 Logs Recentes do Robô BTCUSDT")
    LOG_PATH = r"C:\Users\emgepron\Documents\robo\logs\trading_bot_geneses.log"

    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            linhas = f.readlines()[-20:]
            st.text("".join(linhas))
    else:
        st.info("Sem logs recentes encontrados.")


# =====================================================
# ⚙️ ABA 3 — EDIÇÃO DOS .ENV
# =====================================================
with abas[2]:
    st.header("⚙️ Configurações de Ativos (.env)")

    ENV_DIR = r"C:\Users\emgepron\Documents\robo"

    if not os.path.exists(ENV_DIR):
        st.error(f"❌ Pasta não encontrada: {ENV_DIR}")
    else:
        env_files = [f for f in os.listdir(ENV_DIR) if f.startswith(".env-")]
        if not env_files:
            st.warning("Nenhum arquivo .env-* encontrado no diretório.")
        else:
            selected_env = st.selectbox("Selecione o ativo para editar", env_files)
            env_path = os.path.join(ENV_DIR, selected_env)

            if os.path.exists(env_path):
                config = dotenv_values(env_path)
                st.subheader(f"📄 Parâmetros atuais ({selected_env})")

                # Define quais variáveis podem ser alteradas pelo painel
                editable_keys = [
                    "ATIVO", "MODO_VALIDACAO", "API_IA_URL", "LSTM_API_URL",
                    "ATR_PCT_MIN", "ATR_PCT_MAX", "VOLUME_MINIMO",
                    "RSI_MINIMO", "RSI_MAXIMO",
                    "SCORE_MINIMO_ENTRADA_CONSERVADOR",
                    "SCORE_MINIMO_ENTRADA_MODERADO",
                    "SCORE_MINIMO_ENTRADA_AGRESSIVO",
                    "BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_TESTNET"
                ]

                new_values = {}
                for key, value in config.items():
                    if key in editable_keys:
                        new_val = st.text_input(key, value if value is not None else "")
                        new_values[key] = new_val
                    else:
                        st.text(f"{key}: {value}")

                if st.button("💾 Salvar alterações"):
                    for key, val in new_values.items():
                        set_key(env_path, key, val)
                    st.success(f"✅ {selected_env} atualizado com sucesso!")

                    # Feedback visual instantâneo
                    st.toast(f"Parâmetros salvos para {selected_env}", icon="💾")
            else:
                st.error("Arquivo .env não encontrado.")
