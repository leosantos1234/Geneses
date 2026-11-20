import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logger(modo: str = "producao", nome_arquivo: str = "trading_bot.log"):
    """
    Configura o logger com três modos:
    - 'debug': Console + Arquivo (nível INFO)
    - 'producao': Apenas arquivo (nível INFO)
    - 'silencioso': Apenas arquivo (nível WARNING+)
    """
    logs_dir = "logs"
    os.makedirs(logs_dir, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Limpa handlers anteriores
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Handler para arquivo com rotação
    file_handler = RotatingFileHandler(
        os.path.join(logs_dir, nome_arquivo),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    if modo == "debug":
        file_handler.setLevel(logging.INFO)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    elif modo == "producao":
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

    elif modo == "silencioso":
        file_handler.setLevel(logging.WARNING)
        logger.addHandler(file_handler)

    else:
        raise ValueError("Modo inválido. Use 'debug', 'producao' ou 'silencioso'.")

    logging.info(f"📋 Logger iniciado em modo: {modo.upper()}")

    # Logger separado para IA
    try:
        agente_logger = logging.getLogger("agente_ia")
        if not any(isinstance(h, RotatingFileHandler) for h in agente_logger.handlers):
            agente_handler = RotatingFileHandler(
                os.path.join(logs_dir, "agente_ia.log"),
                maxBytes=2 * 1024 * 1024,
                backupCount=2,
                encoding="utf-8"
            )
            agente_handler.setLevel(logging.INFO)
            agente_handler.setFormatter(formatter)
            agente_logger.addHandler(agente_handler)
            agente_logger.propagate = False
    except Exception as e:
        print(f"⚠️ Erro ao configurar logger da IA: {e}")
