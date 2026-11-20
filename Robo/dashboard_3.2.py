import streamlit as st
import time

def st_autorefresh(interval=3000, key="autorefresh"):
    if f"{key}_last" not in st.session_state:
        st.session_state[f"{key}_last"] = time.time()
    now = time.time()
    if now - st.session_state[f"{key}_last"] >= interval / 1000:
        st.session_state[f"{key}_last"] = now
        st.experimental_rerun()

st_autorefresh(interval=3000, key="autorefresh")
st.write("🕒 Agora:", time.strftime('%H:%M:%S'))
