import logging
import joblib
from trading_bot_rev35 import carregar_dados, treinar_modelo

# Configura logging simples para terminal
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

try:
    logging.info("🔄 Carregando dados para treino...")
    df = carregar_dados()

    if "target" not in df.columns:
        raise ValueError("❌ A coluna 'target' não está presente no DataFrame.")

    X = df.drop(columns=["target"])
    y = df["target"]

    logging.info("⚙️ Iniciando treinamento do modelo...")
    modelo = treinar_modelo(X, y, algorithm="RandomForest")

    logging.info("💾 Salvando modelo treinado...")
    joblib.dump(modelo, "modelo_treinado_20250525_105217.pkl")
    logging.info("✅ Modelo treinado e salvo com sucesso em 'modelo_treinado_20250525_105217.pkl'.")

except Exception as e:
    logging.exception(f"❌ Erro ao treinar o modelo: {e}")
false