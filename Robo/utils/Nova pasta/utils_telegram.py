# utils_telegram.py
import logging
import requests

# ⚠️ Configure com ENV variável depois para segurança
bot_token = "7310063322:AAGKWbwJYA_e_I6Cgu5qBIsE7Ix2i0Q8r60"
chat_id = "1639864401"

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        logging.info("Mensagem enviada com sucesso.")
    else:
        logging.error(f"Erro ao enviar mensagem: {response.text}")
