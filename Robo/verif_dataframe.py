import pandas as pd

def checar_dataframe_para_IA(df, colunas_esperadas, janela_minima=5):
    print("\n--- Checando DataFrame enviado para a IA ---")
    print("Shape:", df.shape)
    print("Colunas:", list(df.columns))
    print("Primeiras linhas:")
    print(df.head())

    # Checar colunas
    colunas_faltando = [c for c in colunas_esperadas if c not in df.columns]
    if colunas_faltando:
        print("❌ Faltando colunas:", colunas_faltando)
    else:
        print("✅ Todas as colunas esperadas estão presentes.")

    # Checar tamanho mínimo
    if len(df) < janela_minima:
        print(f"❌ Tamanho insuficiente ({len(df)}) para janela mínima ({janela_minima})")
    else:
        print("✅ Tamanho suficiente para análise.")

# Exemplo de uso
import pandas as pd

# Simulando um DataFrame de teste
df_exemplo = pd.DataFrame({
    "timestamp": [1,2,3],
    "open": [10,11,12],
    "high": [12,13,14],
    "low": [9,9,10],
    "close": [11,12,13],
    "volume": [100,120,110],
    "SMA_10": [11,11,12],
    "SMA_50": [10,10,11],
    "SMA_20": [11,12,12],
    "std_dev": [1,1,1],
    "Bollinger_Upper": [12,13,14],
    "Bollinger_Lower": [9,10,11]
})

colunas_esperadas = [
    "timestamp", "open", "high", "low", "close", "volume",
    "SMA_10", "SMA_50", "SMA_20", "std_dev",
    "Bollinger_Upper", "Bollinger_Lower"
]

checar_dataframe_para_IA(df_exemplo, colunas_esperadas, janela_minima=3)

