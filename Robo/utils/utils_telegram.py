import logging
import os
import threading
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Configurações do Telegram
# OBS: manter fallback para não quebrar o uso atual, mas priorizar env vars.
_DEFAULT_BOT_TOKEN = "7310063322:AAGKWbwJYA_e_I6Cgu5qBIsE7Ix2i0Q8r60"
_DEFAULT_CHAT_ID = "1639864401"


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _get_telegram_config() -> tuple[Optional[str], Optional[str]]:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", _DEFAULT_BOT_TOKEN).strip() or None
    chat_id = os.getenv("TELEGRAM_CHAT_ID", _DEFAULT_CHAT_ID).strip() or None
    return bot_token, chat_id


def _send_telegram_message_sync(message: str, timeout_s: float) -> None:
    bot_token, chat_id = _get_telegram_config()
    if not bot_token or not chat_id:
        logger.warning("⚠️ Telegram não configurado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID).")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}

    try:
        response = requests.post(url, data=payload, timeout=timeout_s)
        if response.status_code == 200:
            logger.info("Mensagem enviada com sucesso.")
        else:
            logger.warning(f"⚠️ Telegram não aceitou a mensagem: {response.text}")
    except Exception as exc:
        logger.warning(f"⚠️ Falha ao enviar Telegram (ignorado): {exc}")


def send_telegram_message(message: str, *, timeout_s: float = 5.0, async_send: Optional[bool] = None) -> None:
    """
    Envia mensagem via Telegram de forma segura:
    - Timeout curto
    - Fail-safe (nunca levanta exceção)
    - Pode ser assíncrona (thread) para não bloquear o processo
    """
    if not _env_flag("TELEGRAM_ENABLED", default=True):
        return

    if async_send is None:
        async_send = _env_flag("TELEGRAM_ASYNC", default=True)

    if async_send:
        threading.Thread(
            target=_send_telegram_message_sync,
            args=(str(message), float(timeout_s)),
            daemon=True,
            name="telegram-notify",
        ).start()
        return

    _send_telegram_message_sync(str(message), float(timeout_s))
