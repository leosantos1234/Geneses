import logging
from trading_bot import iniciar_robo  # Certifique-se de que o nome do arquivo e da função estão corretos

# Configuração de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

if __name__ == "__main__":
    if "iniciar_robo" in globals():
        logging.info("Função iniciar_robo encontrada. Executando...")
        iniciar_robo()
    else:
        logging.error("Função iniciar_robo não definida.")
