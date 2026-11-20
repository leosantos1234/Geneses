import os
import sys
import itertools
import json
from datetime import datetime
from automl.utils_automl import ler_env, salvar_env, rodar_backtest, salvar_resultado

# 1. Recebe o ativo por argumento de linha de comando
if len(sys.argv) < 2:
    print("Uso: python automl_geral.py <ativo>")
    print("Exemplo: python automl_geral.py btc")
    sys.exit(1)

ativo = sys.argv[1].lower()  # btc, eth, sol...

# 2. Configura caminhos dinâmicos
caminho_dir = f"automl/{ativo}"
os.makedirs(caminho_dir, exist_ok=True)
os.makedirs(f"{caminho_dir}/best_configs", exist_ok=True)
os.makedirs(f"{caminho_dir}/logs", exist_ok=True)
os.makedirs(f"{caminho_dir}/results", exist_ok=True)
os.makedirs(f"{caminho_dir}/search_spaces", exist_ok=True)

env_base = os.path.abspath(os.path.join(os.path.dirname(__file__), f"../.env-{ativo}"))
env_teste = f"{caminho_dir}/.env-{ativo}-teste"
resultados_path = f"{caminho_dir}/results/resultados_{ativo}.json"
log_path = f"{caminho_dir}/logs/automl_{ativo}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
search_space_path = f"{caminho_dir}/search_spaces/space_{ativo}.json"

# 3. Define espaços de busca personalizados por ativo (ajuste conforme quiser)
espacos_busca = {
    "btc": {
        "ATR_MIN": [30, 40, 50],
        "RRR": [1.5, 2.0, 2.5],
        "SCORE_MINIMO": [5, 7, 8],
    },
    "eth": {
        "ATR_MIN": [20, 25, 30],
        "RRR": [1.2, 1.5, 2.0],
        "SCORE_MINIMO": [6, 8, 10],
    },
    "sol": {
        "ATR_MIN": [10, 15, 20],
        "RRR": [1.0, 1.3, 1.6],
        "SCORE_MINIMO": [5, 6, 8],
    }
}

search_space = espacos_busca.get(ativo)
if search_space is None:
    print(f"Ativo {ativo} não está configurado. Adicione no dicionário 'espacos_busca'.")
    sys.exit(1)

with open(search_space_path, "w", encoding="utf-8") as f:
    json.dump(search_space, f, indent=4)

# 4. Carrega parâmetros base
param_atual = ler_env(env_base)
param_names = list(search_space.keys())
combinacoes = list(itertools.product(*search_space.values()))

melhor_lucro = -float('inf')
melhor_param = None

with open(log_path, "w", encoding="utf-8") as flog:
    for comb in combinacoes:
        params = param_atual.copy()
        for i, name in enumerate(param_names):
            params[name] = comb[i]

        salvar_env(env_teste, params)
        resultado = rodar_backtest(env_teste)
        salvar_resultado(resultados_path, params, resultado)
        flog.write(f"Tentativa: {params} => {resultado}\n")

        if resultado["lucro"] > melhor_lucro:
            melhor_lucro = resultado["lucro"]
            melhor_param = params.copy()

timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
best_config_path = f"{caminho_dir}/best_configs/.env-{ativo}-sugerido-{timestamp}"
salvar_env(best_config_path, melhor_param)
print(f"Melhor config sugerida salva em {best_config_path}")
