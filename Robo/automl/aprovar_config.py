import shutil
import sys
from datetime import datetime
import json
import os

def mostrar_relatorio(ativo):
    result_path = f"automl/{ativo}/results/resultados_{ativo}.json"
    if not os.path.exists(result_path):
        print("\nNenhum relatório de resultado encontrado.\n")
        return

    try:
        with open(result_path, encoding="utf-8") as f:
            resultados = json.load(f)
            if isinstance(resultados, dict):
                resultados = list(resultados.values())
            if not resultados:
                print("\nNenhum resultado no relatório.\n")
                return
            ultimo = resultados[-1]
            res = ultimo.get('resultado', ultimo)
            print("\n===== RELATÓRIO DA CONFIGURAÇÃO =====")
            print(f"Win Rate:      {res.get('winrate', '--')}%")
            print(f"RRR:           {res.get('rrr', '--')}")
            print(f"Nº Trades:     {res.get('num_trades', '--')}")
            print(f"Lucro:         {res.get('lucro', '--')}")
            print(f"Drawdown:      {res.get('drawdown', '--')}")
            print("=====================================\n")
    except Exception as e:
        print(f"\nErro ao ler relatório: {e}\n")

def exportar_relatorio_txt(ativo, output_path=None):
    result_path = f"automl/{ativo}/results/resultados_{ativo}.json"
    if not os.path.exists(result_path):
        print("\nNenhum relatório de resultado encontrado.\n")
        return

    try:
        with open(result_path, encoding="utf-8") as f:
            resultados = json.load(f)
            if isinstance(resultados, dict):
                resultados = list(resultados.values())
            if not resultados:
                print("\nNenhum resultado no relatório.\n")
                return
            ultimo = resultados[-1]
            res = ultimo.get('resultado', ultimo)
            linhas = []
            linhas.append("===== RELATÓRIO DA CONFIGURAÇÃO =====")
            linhas.append(f"Win Rate:      {res.get('winrate', '--')}%")
            linhas.append(f"RRR:           {res.get('rrr', '--')}")
            linhas.append(f"Nº Trades:     {res.get('num_trades', '--')}")
            linhas.append(f"Lucro:         {res.get('lucro', '--')}")
            linhas.append(f"Drawdown:      {res.get('drawdown', '--')}")
            linhas.append("=====================================")
            if output_path is None:
                output_path = f"relatorio_{ativo}.txt"
            with open(output_path, "w", encoding="utf-8") as txtf:
                txtf.write("\n".join(linhas))
            print(f"\nRelatório salvo em: {output_path}\n")
    except Exception as e:
        print(f"\nErro ao exportar relatório: {e}\n")

def aprovar_config(ativo):
    from glob import glob

    pasta_best = f"automl/{ativo}/best_configs/"
    arquivos = sorted(glob(f"{pasta_best}/.env-{ativo}-sugerido-*"), reverse=True)
    if not arquivos:
        print(f"Nenhuma config sugerida encontrada para {ativo}.")
        return
    best_config = arquivos[0]

    atual = f".env-{ativo}"
    if os.path.exists(atual):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bkp = f"{atual}-bkp-{timestamp}"
        shutil.copyfile(atual, bkp)
        print(f"Backup criado: {bkp}")

    shutil.copyfile(best_config, atual)
    print(f"Config aprovada e aplicada: {atual} (de {best_config})")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python aprovar_config.py <ativo>")
        sys.exit(1)
    ativo = sys.argv[1]
    mostrar_relatorio(ativo)      # Mostra o relatório no terminal
    exportar_relatorio_txt(ativo) # Salva o .txt para o Notepad
    aprovar_config(ativo)
