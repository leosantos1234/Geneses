import logging
logger = logging.getLogger(__name__)  # Logger do módulo

import os
import sys
from datetime import datetime


def _normalize_log_text(text):
    """Repair UTF-8 text that was decoded as latin-1/cp1252."""
    if not isinstance(text, str):
        return text
    markers = tuple(chr(code) for code in (0x00c3, 0x00c2, 0x00e2, 0x00f0, 0x00ef))
    if not any(marker in text for marker in markers):
        return text
    repaired = text
    for _ in range(2):
        try:
            candidate = repaired.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


class SafeUTF8Formatter(logging.Formatter):
    def format(self, record):
        return _normalize_log_text(super().format(record))


def _configure_utf8_stream(stream):
    """Keep console logging readable on Windows terminals."""
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    return stream


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

    file_handler = logging.FileHandler(caminho_log, encoding="utf-8")
    file_handler.setFormatter(SafeUTF8Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    handlers = [file_handler]
    if modo in ["debug", "info"]:
        stream_handler = logging.StreamHandler(_configure_utf8_stream(sys.__stdout__))
        stream_handler.setFormatter(SafeUTF8Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        handlers.append(stream_handler)

    logging.basicConfig(
        level=nivel,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers
    )

    return logger_raiz
