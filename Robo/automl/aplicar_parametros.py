from utils_automl import ler_env, salvar_env
import shutil
from datetime import datetime

caminho_env = ".env-btc"

# 1. Backup automático
def backup_env(caminho_env):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{caminho_env}-bkp-{timestamp}"
    shutil.copyfile(caminho_env, backup_path)
    print(f"Backup criado: {backup_path}")

# 2. Aplicar novos parâmetros
def aplicar_novos_parametros(params):
    backup_env(caminho_env)
    salvar_env(caminho_env, params)
    print(f"Novos parâmetros aplicados em {caminho_env}")

# Exemplo de uso:
params = ler_env("automl/.env-btc-sugerido")  # ou resultados do AutoML
aplicar_novos_parametros(params)
