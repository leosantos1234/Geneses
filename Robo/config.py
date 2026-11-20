import logging
import os
# === CONFIGURAÇÃO DE LOG ===
from utils.logger import configurar_logger
import logging
from dotenv import load_dotenv
import os

# ⚠️ garante carregamento ANTES de ler variáveis
load_dotenv()

from exchange_config import get_active_exchange, validate_active_exchange

ACTIVE_EXCHANGE = get_active_exchange()
validate_active_exchange()

# Inicializa o logger principal em modo info (arquivo + console limpo)
logger = configurar_logger(modo="info")

# Logger separado para eventos específicos da IA
logger_ia = logging.getLogger("agente_ia")
logger_ia.setLevel(logging.INFO)

logger.info("⚙️ Configurações carregadas com sucesso.")



BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

if ACTIVE_EXCHANGE == "bybit" and (not BYBIT_API_KEY or not BYBIT_API_SECRET):
    raise RuntimeError("❌ BYBIT_API_KEY ou BYBIT_API_SECRET não carregadas do .env")



# 🌐 Ambiente de execução
_testnet_raw = str(os.getenv("BYBIT_TESTNET", "false")).strip().lower()
TESTNET = _testnet_raw in ("1", "true", "yes", "on", "sim")




# Configurações Gerais
LOG_FILE = "trading_bot.log"  # Nome do arquivo de log
LOG_LEVEL = logging.INFO  # Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)

# Configurações de Trading
TRADING_SYMBOL = os.getenv("ATIVOS", "BTCUSDT").strip().upper()
MAX_TRADES_PER_DAY = 5  # Número máximo de trades permitidos por dia
LOSS_LIMIT_PERCENT = 0.01  # Perda máxima por operação (1% do saldo total)
RRR_MINIMA = 2  # Relação Risco/Recompensa mínima

# Configurações de Indicadores
ATR_MULTIPLICADOR_STOP_LOSS = 1.5  # Multiplicador do ATR para Stop-Loss
ATR_MULTIPLICADOR_TAKE_PROFIT = 3  # Multiplicador do ATR para Take-Profit

# Configurações de Mensagens
TELEGRAM_BOT_TOKEN = "seu_token_aqui"  # Substitua pelo token do bot do Telegram
TELEGRAM_CHAT_ID = "seu_chat_id_aqui"  # Substitua pelo chat ID do Telegram

# Configurações de Tempo
TIME_BETWEEN_TRADES = 300  # Tempo entre operações (em segundos)
TIME_BETWEEN_CHECKS = 60  # Tempo entre verificações de mercado (em segundos)


