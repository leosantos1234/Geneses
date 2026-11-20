import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler
from indicadores_tecnicos11 import calculate_indicators  # Função para calcular indicadores técnicos

# Função para avaliar o mercado
def avaliar_mercado_com_modelo(df, modelo, colunas_treinamento):
    try:
        # Extraindo os últimos dados para avaliação com nomes de colunas
        ultimo_dado = pd.DataFrame([df.iloc[-1][colunas_treinamento]], columns=colunas_treinamento)
        predicao = modelo.predict(ultimo_dado)

        # Convertendo a previsão em ação
        acao_modelo = 'buy' if predicao == 1 else 'sell'
        return acao_modelo
    except Exception as e:
        print(f"Erro ao avaliar o mercado: {e}")
        return None

# Lista de colunas necessárias para o modelo
colunas_treinamento = [
    'close', 'volume', 'SMA', 'RSI', 'ATR',
    'EMA12', 'EMA26', 'MACD', 'Signal_Line',
    'Bollinger_Upper', 'Bollinger_Lower'
]

# Criando dados fictícios para teste
data = {
    "close": [100, 102, 101, 103, 102],
    "volume": [1000, 1100, 1050, 1200, 1150],
    "high": [101, 103, 102, 104, 103],
    "low": [99, 101, 100, 102, 101],
}
df = pd.DataFrame(data)

# Calculando os indicadores técnicos
print("Calculando indicadores técnicos...")
df = calculate_indicators(df)
df.fillna(0, inplace=True)  # Preenchendo valores NaN

# Transformando os dados para treinamento com nomes de colunas
scaler = MinMaxScaler()
X = pd.DataFrame(scaler.fit_transform(df[colunas_treinamento]), columns=colunas_treinamento)
y = [1, 0, 1, 0, 1]  # Exemplo de rótulos para compra/venda

# Dividindo em treino e teste
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

# Treinando o modelo
modelo = RandomForestClassifier(random_state=42)
modelo.fit(X_train, y_train)

# Avaliando o mercado
print("Avaliando o mercado...")
acao = avaliar_mercado_com_modelo(df, modelo, colunas_treinamento)

# Exibindo o resultado
if acao:
    print(f"Ação sugerida pelo modelo: {acao}")
else:
    print("Nenhuma ação sugerida pelo modelo.")
