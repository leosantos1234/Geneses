import numpy as np

def preparar_candles_para_lstm(lista_candles, n_candles=10, n_features=4):
    arr = np.array(lista_candles, dtype=np.float32)
    # Verifica se tem NaN ou shape estranho
    if arr.shape != (n_candles, n_features):
        raise ValueError(f"Shape inválido para LSTM: {arr.shape}. Esperado: ({n_candles},{n_features})")
    if np.isnan(arr).any():
        raise ValueError("Dados contém valores NaN!")
    return arr.reshape((1, n_candles, n_features))
