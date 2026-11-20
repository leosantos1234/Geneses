import os
import joblib
import hashlib
from datetime import datetime

# Caminho absoluto para o modelo em produção
MODELO_PATH = r"C:\Users\emgepron\Documents\robo\Modelos\modelo_treinado.pkl"

def info_modelo(modelo_path):
    if not os.path.exists(modelo_path):
        print(f"❌ Modelo não encontrado: {modelo_path}")
        return

    stat = os.stat(modelo_path)
    mod_time = datetime.fromtimestamp(stat.st_mtime)
    size = stat.st_size

    with open(modelo_path, "rb") as f:
        file_hash = hashlib.md5(f.read()).hexdigest()

    print("\n=== INFORMAÇÕES DO MODELO EM PRODUÇÃO ===")
    print(f"Arquivo         : {modelo_path}")
    print(f"Última modificação : {mod_time}")
    print(f"Tamanho         : {size:,} bytes")
    print(f"Hash MD5        : {file_hash}")

    try:
        modelo = joblib.load(modelo_path)
        print("Modelo carregado com sucesso!")
        # Detalhe extra (ex: features usadas, se for scikit-learn):
        if hasattr(modelo, "feature_names_in_"):
            print("Features do modelo:", list(modelo.feature_names_in_))
    except Exception as e:
        print("Erro ao carregar modelo:", e)

if __name__ == "__main__":
    info_modelo(MODELO_PATH)
