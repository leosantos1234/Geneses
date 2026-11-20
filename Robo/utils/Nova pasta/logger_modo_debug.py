import logging
import os

def setup_logger():
    logs_dir = "logs"
    os.makedirs(logs_dir, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # ✅ File Handler (arquivo)
    try:
        file_handler = logging.FileHandler(os.path.join(logs_dir, "trading_bot.log"), encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"⚠️ Falha ao configurar log principal: {e}")

    # ✅ Console Handler (terminal)
    try:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    except Exception as e:
        print(f"⚠️ Falha ao configurar log no console: {e}")

    # ✅ Log do agente IA
    try:
        agente_logger = logging.getLogger("agente_ia")
        if not any(isinstance(h, logging.FileHandler) for h in agente_logger.handlers):
            agente_handler = logging.FileHandler(os.path.join(logs_dir, "agente_ia.log"), encoding="utf-8")
            agente_handler.setLevel(logging.INFO)
            agente_handler.setFormatter(formatter)
            agente_logger.addHandler(agente_handler)
            agente_logger.propagate = False
    except Exception as e:
        print(f"⚠️ Falha ao configurar log do agente IA: {e}")
