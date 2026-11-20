import logging
import sys
import os
from datetime import datetime

def configurar_logger_global(nome_arquivo: str = "trading_bot_geneses.log", modo="info"):
    logger = logging.getLogger()
    # REMOVE TODOS OS HANDLERS ANTES DE ADICIONAR NOVOS
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    os.makedirs("logs", exist_ok=True)
    data = datetime.now().strftime("%Y-%m-%d")
    caminho_log = os.path.join("logs", f"{data}_{nome_arquivo}")

    nivel = logging.DEBUG if modo == "debug" else logging.INFO
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Console
    stream_handler = logging.StreamHandler(sys.__stdout__)
    stream_handler.setFormatter(formatter)
    # Arquivo
    file_handler = logging.FileHandler(caminho_log, encoding='utf-8')
    file_handler.setFormatter(formatter)

    logger.setLevel(nivel)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
