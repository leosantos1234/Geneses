import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from datetime import datetime
import json

# Caminho correto para o CSV de treino
CAMINHO_DADOS = "log_trade/dados_para_treinamento.csv"
df = pd.read_csv(CAMINHO_DADOS)

# 🔒 Verifica se o DataFrame está vazio
if df.empty:
    raise ValueError("❌ ERRO: DataFrame está vazio. Não é possível treinar o modelo.")

# Suponha que as features e o target estejam definidos
X = df.drop("target", axis=1)
y = df["target"]

# Treina o modelo
modelo = RandomForestClassifier(n_estimators=100, random_state=42)
modelo.fit(X, y)

# ✅ Salva o modelo com timestamp
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
joblib.dump(modelo, f"modelo_treinado_{timestamp}.pkl")

# 📊 Avalia a acurácia
acuracia = modelo.score(X, y)

# 📝 Cria e salva o log de desempenho
log_resultado = {
    "timestamp": timestamp,
    "acuracia": round(acuracia, 4),
    "total_amostras": len(df)
}

with open(f"resultado_treinamento_{timestamp}.json", "w") as f:
    json.dump(log_resultado, f, indent=4)

print("✅ Modelo treinado e salvo com sucesso!")
print("📈 Acurácia:", round(acuracia, 4))
