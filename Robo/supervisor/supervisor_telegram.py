import logging
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from supervisor.comandos import processar_comando  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
last_update_id = None


def _get_bot_token() -> str:
    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado.")
    return token


def _telegram_api(method: str) -> str:
    return f"https://api.telegram.org/bot{_get_bot_token()}/{method}"


def enviar_resposta(chat_id: int | str, texto: str) -> None:
    try:
        requests.post(
            _telegram_api("sendMessage"),
            data={"chat_id": str(chat_id), "text": str(texto)},
            timeout=10,
        )
    except Exception as e:
        logger.warning("Falha ao enviar resposta para Telegram: %s", e)


def obter_updates(offset: int | None = None) -> list[dict]:
    params = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset

    try:
        resp = requests.get(_telegram_api("getUpdates"), params=params, timeout=35)
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            logger.warning("Telegram retornou erro em getUpdates: %s", payload)
            return []
        return payload.get("result", []) or []
    except Exception as e:
        logger.warning("Falha ao obter updates do Telegram: %s", e)
        time.sleep(3)
        return []


def extrair_mensagem(update: dict) -> tuple[int | None, int | None, str]:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    texto = str(message.get("text") or "").strip()
    return chat_id, user_id, texto


def main() -> None:
    global last_update_id
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    print("ENV_PATH:", ENV_PATH)
    print("USER_ID:", os.getenv("TELEGRAM_SUPERVISOR_USER_ID"))
    logger.info("Supervisor Telegram iniciado.")

    TELEGRAM_SUPERVISOR_USER_ID = os.getenv("TELEGRAM_SUPERVISOR_USER_ID")
    if not TELEGRAM_SUPERVISOR_USER_ID:
        raise RuntimeError("TELEGRAM_SUPERVISOR_USER_ID não configurado.")

    while True:
        updates = obter_updates(offset=(last_update_id + 1) if last_update_id is not None else None)
        print("Updates recebidos:", updates)
        for update in updates:
            last_update_id = int(update.get("update_id", 0))
            chat_id, user_id, texto = extrair_mensagem(update)
            if not chat_id or not texto:
                continue

            print(f"[Telegram] user_id={user_id} | texto={texto}")
            logger.info("Update recebido | chat_id=%s | user_id=%s | texto=%s", chat_id, user_id, texto)
            resposta = processar_comando(texto, user_id)
            if resposta:
                enviar_resposta(chat_id, resposta)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Supervisor Telegram encerrado manualmente.")
    except Exception as e:
        logger.exception("Supervisor Telegram finalizado por erro")
        raise SystemExit(f"Falha ao iniciar supervisor: {e}")
