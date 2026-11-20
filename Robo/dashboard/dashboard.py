import streamlit as st
import json

st.set_page_config(page_title="Painel Resumido do Robô", layout="centered")

st.title("Painel Resumido do Robô")
st.markdown("Visualize cada etapa dos trades em tempo real! 🔥")

eventos = []
try:
    with open("eventos.json", "r", encoding="utf-8") as f:
        for line in f:
            eventos.append(json.loads(line))
except FileNotFoundError:
    st.warning("Ainda não há eventos registrados.")

for ev in eventos[-20:]:  # Mostra só os últimos 20 eventos
    st.write(f"**[{ev['hora']}]** {ev['etapa']}: {ev['status']}")
    if ev['detalhes']:
        st.caption(ev['detalhes'])

# Atualização automática a cada 10 segundos
st.experimental_rerun = lambda: None  # Streamlit não tem autorefresh nativo, mas plugins resolvem
