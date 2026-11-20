import logging
logger = logging.getLogger(__name__)  # Logger do módulo

import os
import sys
from datetime import datetime

def configurar_logger(modo="info", nome_arquivo="trading_bot.log"):
    """
    Configura o logger global.
    """
    logger_raiz = logging.getLogger()  # Logger global/root

    if logger_raiz.hasHandlers():
        for handler in logger_raiz.handlers[:]:
            logger_raiz.removeHandler(handler)

    os.makedirs("logs", exist_ok=True)
    data = datetime.now().strftime("%Y-%m-%d")
    caminho_log = os.path.join("logs", f"{data}_{nome_arquivo}")

    nivel = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "producao": logging.INFO,
        "silencioso": logging.CRITICAL
    }.get(modo.lower(), logging.INFO)

    handlers = [logging.FileHandler(caminho_log, encoding="utf-8")]
    if modo in ["debug", "info"]:
        handlers.append(logging.StreamHandler(sys.__stdout__))

    logging.basicConfig(
        level=nivel,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers
    )

    return logger_raiz
