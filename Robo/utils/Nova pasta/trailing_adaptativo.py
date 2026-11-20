def calcular_trailing_adaptativo(atr, tipo_stop, contexto=None):
    """
    Define o trailing stop ideal conforme o tipo de stop/contexto.
    - atr: valor do ATR atual (float)
    - tipo_stop: string ("ATR", "suporte_resistencia", "bollinger", "percentual", etc)
    - contexto: opcional, string para afinar o ajuste

    Retorna o valor do trailing_offset.
    """
    # Multiplicadores recomendados (ajuste conforme seu perfil)
    multipliers = {
        "ATR": 0.5,
        "suporte_resistencia": 0.35,
        "bollinger": 0.7,
        "percentual": 0.45,
        "fallback": 0.4
    }
    # Permite afinar por contexto se quiser (opcional)
    if contexto == "volatilidade_alta":
        return round(atr * 0.7)
    elif contexto == "lateral":
        return round(atr * 0.3)
    elif contexto == "range_bollinger":
        return round(atr * 0.8)

    # Usa o multiplicador conforme o tipo_stop
    mult = multipliers.get(tipo_stop, multipliers["fallback"])
    return round(atr * mult)
