import logging
import os
import threading
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Configurações do Telegram
# As credenciais devem vir exclusivamente do ambiente.
# Não manter tokens ou IDs reais no código-fonte.


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _get_telegram_config() -> tuple[Optional[str], Optional[str]]:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
    # Mantém compatibilidade com a configuração atual sem guardar o ID no código.
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID", "").strip()
        or os.getenv("TELEGRAM_SUPERVISOR_USER_ID", "").strip()
        or None
    )
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
        if response.status_code != 200:
            logger.warning(f"⚠️ Telegram não aceitou a mensagem: {response.text}")
    except Exception as exc:
        logger.warning(f"⚠️ Falha ao enviar Telegram (ignorado): {exc}")


def send_telegram_message(message: str, *, timeout_s: float = 5.0, async_send: Optional[bool] = None) -> None:
    """
    Envia mensagem via Telegram de forma segura:
    - NUNCA roda no import (somente quando chamada)
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


def notificar_inicio() -> None:
    """Notificação explícita de start (não é chamada automaticamente no import)."""
    send_telegram_message("Iniciando - Geneses Quant...")


if __name__ == "__main__":
    notificar_inicio()
