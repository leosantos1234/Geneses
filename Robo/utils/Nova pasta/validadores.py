# utils/validadores.py

def validar_indicadores(df):
    """
    Verifica se os principais indicadores técnicos estão presentes e sem valores nulos.
    """
    colunas = ['close', 'volume', 'SMA_10', 'SMA_50', 'RSI', 'ATR']
    return all(col in df.columns and not df[col].isnull().any() for col in colunas)

def validar_indicadores_com_reset(df, operacao_em_andamento_flag=None):
    """
    Valida os indicadores e reseta a flag da operação se necessário.
    """
    if not validar_indicadores(df):
        if operacao_em_andamento_flag is not None:
            operacao_em_andamento_flag[0] = False  # usar lista para permitir mutabilidade externa
        return False
    return True

def verificar_parametros_ordem(symbol, qty, side, price=None):
    """
    Valida os parâmetros essenciais de uma ordem.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("❌ Símbolo inválido.")
    if not isinstance(qty, (int, float)) or qty <= 0:
        raise ValueError("❌ Quantidade deve ser um número positivo.")
    if side not in ("buy", "sell"):
        raise ValueError("❌ Lado da ordem deve ser 'buy' ou 'sell'.")
    if price is not None and (not isinstance(price, (int, float)) or price <= 0):
        raise ValueError("❌ Preço deve ser positivo se fornecido.")
    return True
