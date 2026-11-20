# utils/config_flags.py
import os


# Converte string de ambiente em booleano
def parse_bool(value: str, default=False):
    return str(value).strip().lower() in ("1", "true", "yes") if value else default

# Modo backtest
BACKTEST_MODE = parse_bool(os.getenv("BACKTEST_MODE", "false"))

