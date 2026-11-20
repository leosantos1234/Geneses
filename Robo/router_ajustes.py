# 📁 router_ajustes.py
from fastapi import APIRouter, Request
import logging


router = APIRouter()

# Variáveis globais (ou você pode persistir isso em arquivo ou banco se quiser)
SL_GLOBAL = 1.5
TP_GLOBAL = 3.0

@router.post("/ajustar_sl_tp")
async def ajustar_sl_tp(request: Request):
    global SL_GLOBAL, TP_GLOBAL

    dados = await request.json()
    sl = dados.get("stop_loss")
    tp = dados.get("take_profit")

    if sl is not None:
        SL_GLOBAL = sl
    if tp is not None:
        TP_GLOBAL = tp

    logging.info(f"🎚️ SL atualizado para: {SL_GLOBAL}%, TP para: {TP_GLOBAL}%")
    return {"status": "ok", "stop_loss": SL_GLOBAL, "take_profit": TP_GLOBAL}
