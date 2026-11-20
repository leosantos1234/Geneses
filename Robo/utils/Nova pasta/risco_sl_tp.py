EPS = 1e-6  # tolerância numérica

def distancia_valida(distancia, minimo):
    return (distancia + EPS) >= minimo

def round_to_tick(price, tick):
    return round(price / tick) * tick if tick > 0 else price

def calc_sl_tp(entry, side, atr=None, rr=2.0, min_dist=30, tick=0.1):
    """
    Calcula SL e TP com base no lado, ATR e regras mínimas de distância e tickSize.

    Args:
        entry (float): preço de entrada.
        side (str): 'BUY' ou 'SELL'.
        atr (float): valor do ATR (usado como base de risco).
        rr (float): risk:reward ratio.
        min_dist (float): distância mínima entre entrada e SL/TP.
        tick (float): tamanho do tick (tickSize da corretora).

    Returns:
        tuple: (stop_loss, take_profit)
    """
    risco_base = max(atr or entry * 0.005, min_dist)

    if side.upper() == 'BUY':
        sl = entry - risco_base
        tp = entry + risco_base * rr
        dist_sl = entry - sl
        dist_tp = tp - entry
    elif side.upper() == 'SELL':
        sl = entry + risco_base
        tp = entry - risco_base * rr
        dist_sl = sl - entry
        dist_tp = entry - tp
    else:
        raise ValueError("Side inválido. Use 'BUY' ou 'SELL'.")

    if not distancia_valida(dist_sl, min_dist):
        falta = min_dist - dist_sl
        sl = sl - falta if side.upper() == "BUY" else sl + falta

    if not distancia_valida(dist_tp, min_dist):
        falta = min_dist - dist_tp
        tp = tp + falta if side.upper() == "BUY" else tp - falta

    sl = round_to_tick(sl, tick)
    tp = round_to_tick(tp, tick)

    return sl, tp
