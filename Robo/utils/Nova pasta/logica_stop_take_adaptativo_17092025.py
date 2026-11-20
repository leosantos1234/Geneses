import numpy as np
import pandas as pd
import logging
import os

# Parâmetros globais (pode ser sobrescrito pelo sistema ou IA)
PARAMS = {
    "atr_min": float(os.getenv("ATR_MIN", 150)),
    "atr_mult": float(os.getenv("ATR_MULT", 0.85)),  # <-- lembre do ATR_MULT!
    "sl_pct_min": float(os.getenv("SL_PCT_MIN", 0.006)),
    "sl_pct_max": float(os.getenv("SL_PCT_MAX", 0.012)),
    "rrr": float(os.getenv("RRR", 1.8)),
    "sr_lookback": int(os.getenv("SR_LOOKBACK", 12)),
    "bb_dist_min": float(os.getenv("BB_DIST_MIN", 30)),
    "bb_mult": float(os.getenv("BB_MULT", 1.05)),
    "fixo_pct": float(os.getenv("FIXO_PCT", 0.0035)),
}

def indicadores_para_dict(indicadores):
    """Converte indicadores em floats, ignorando None/NaN/Series vazias."""
    saida = {}
    for k, v in indicadores.items():
        if isinstance(v, pd.Series):
            v = v.iloc[-1] if not v.empty else None
        if v is None:
            continue
        try:
            v_float = float(v)
            if np.isnan(v_float):
                continue
            saida[k] = v_float
        except Exception:
            continue
    return saida

def selecionar_tipo_stop(contexto):
    """Define o tipo de stop conforme o contexto detectado."""
    if contexto == "volatilidade_alta":
        return "ATR"
    if contexto == "lateral":
        return "suporte_resistencia"
    if contexto == "range_bollinger":
        return "bollinger"
    if contexto == "tendencia_direcional":
        return "percentual"
    return "ATR"


def calcular_bollinger(df, periodo=20, mult=2):
    if len(df) < periodo:
        # Não é possível calcular ainda, retorna as colunas como NaN
        df["BB_Upper"] = np.nan
        df["BB_Lower"] = np.nan
        return df
    # Calcula média e desvio padrão
    df["BB_Middle"] = df["close"].rolling(periodo).mean()
    df["BB_Std"] = df["close"].rolling(periodo).std()
    df["BB_Upper"] = df["BB_Middle"] + mult * df["BB_Std"]
    df["BB_Lower"] = df["BB_Middle"] - mult * df["BB_Std"]
    # Preenche possíveis NaN do início (se for usar em pipeline real-time)
    df["BB_Upper"] = df["BB_Upper"].bfill()
    df["BB_Lower"] = df["BB_Lower"].bfill()
    return df



def detectar_contexto_mercado(df):
    """
    Detecta regime de mercado robustamente, mesmo com indicadores faltando.
    Retorna 'desconhecido' só se não houver alternativa.
    """
    try:
        # Passo 1: Garante todas as colunas essenciais!
        colunas_essenciais = ["ATR", "BB_Upper", "BB_Lower", "volume", "RSI", "close", "low", "high"]
        for col in colunas_essenciais:
            if col not in df.columns:
                df[col] = np.nan
        df = calcular_bollinger(df)

        # Passo 2: Captura os valores
        atr = df["ATR"].iloc[-1]
        atr_medio = df["ATR"].rolling(20).mean().iloc[-1]
        bb_upper = df["BB_Upper"].iloc[-1]
        bb_lower = df["BB_Lower"].iloc[-1]
        bb_largura = bb_upper - bb_lower
        volume = df["volume"].iloc[-1]
        volume_medio = df["volume"].rolling(20).mean().iloc[-1]
        close = df["close"].iloc[-1]
        rsi = df["RSI"].iloc[-1] if "RSI" in df.columns else 50

        # Passo 3: Checagem de validade
        for nome, val in [("ATR", atr), ("ATR_medio", atr_medio), ("BB_Upper", bb_upper), ("BB_Lower", bb_lower), ("volume", volume), ("volume_medio", volume_medio), ("close", close)]:
            if val is None or np.isnan(val):
                logging.warning(f"[Contexto] {nome} está None/NaN! Usando fallback para contexto mais seguro")
                return "suporte_resistencia"  # Fallback mais seguro

        # Passo 4: Detecta contexto
        if atr > 1.5 * atr_medio and volume > 1.2 * volume_medio:
            return "volatilidade_alta"
        if bb_largura < PARAMS["bb_dist_min"] and atr < 0.7 * atr_medio:
            return "lateral"
        if bb_largura > 2 * atr and abs(rsi - 50) < 7:
            return "range_bollinger"
        if abs(close - df["close"].iloc[-20]) > 2 * atr_medio:
            return "tendencia_direcional"
        if not np.isnan(bb_largura) and bb_largura > 0:
            return "bollinger"
        return "suporte_resistencia"
    except Exception as e:
        logging.error(f"Erro no detectar_contexto_mercado: {e}", exc_info=True)
        return "ATR"  # nunca deixa o sistema travar, mas ATR só se tudo der ruim!

def calcular_stop_take_adaptativo(df, entry_price, side, params=None, tipo_stop=None, debug=False):
    """
    Calcula Stop-Loss e Take-Profit adaptativos.
    - df: DataFrame com colunas ATR, close, volume, BB_Upper, BB_Lower, RSI, etc.
    - entry_price: preço de entrada (float)
    - side: "buy" ou "sell"
    - params: dicionário de hiperparâmetros
    """
    p = {**PARAMS, **(params or {})}
    contexto = detectar_contexto_mercado(df)
    if tipo_stop is None:
        tipo_stop = selecionar_tipo_stop(contexto)
    atr = float(df["ATR"].iloc[-1])
    atr = max(atr, p["atr_min"])
    rrr = p["rrr"]
    stop_loss, take_profit = None, None

    if debug:
        logging.info(f"Contexto detectado: {contexto} | Tipo de Stop: {tipo_stop}")

    # --------- ATR -----------
    if tipo_stop == "ATR":
        dist = max(atr * p["atr_mult"], entry_price * p["sl_pct_min"])
        dist = min(dist, entry_price * p["sl_pct_max"])
        if side == "buy":
            stop_loss = entry_price - dist
            take_profit = entry_price + dist * rrr
        else:
            stop_loss = entry_price + dist
            take_profit = entry_price - dist * rrr

    # ----- Suporte/Resistência -----
    elif tipo_stop == "suporte_resistencia":
        if side == "buy":
            stop_loss = df["low"].rolling(p["sr_lookback"]).min().iloc[-2]
            dist = entry_price - stop_loss
            take_profit = entry_price + dist * rrr
        else:
            stop_loss = df["high"].rolling(p["sr_lookback"]).max().iloc[-2]
            dist = stop_loss - entry_price
            take_profit = entry_price - dist * rrr
        min_dist = max(atr * 0.7, entry_price * p["sl_pct_min"])
        if abs(entry_price - stop_loss) < min_dist:
            stop_loss = entry_price - min_dist if side == "buy" else entry_price + min_dist
            take_profit = entry_price + min_dist * rrr if side == "buy" else entry_price - min_dist * rrr

    # ------- Bollinger Bands -------
    elif tipo_stop == "bollinger":
        bb_lower = df["BB_Lower"].iloc[-1]
        bb_upper = df["BB_Upper"].iloc[-1]
        if side == "buy":
            stop_loss = min(bb_lower, entry_price - atr * p["bb_mult"])
            dist = entry_price - stop_loss
            take_profit = entry_price + dist * rrr
        else:
            stop_loss = max(bb_upper, entry_price + atr * p["bb_mult"])
            dist = stop_loss - entry_price
            take_profit = entry_price - dist * rrr

    # ------- Percentual fixo -------
    elif tipo_stop == "percentual":
        dist = max(entry_price * p["fixo_pct"], atr * 0.7)
        if side == "buy":
            stop_loss = entry_price - dist
            take_profit = entry_price + dist * rrr
        else:
            stop_loss = entry_price + dist
            take_profit = entry_price - dist * rrr

    # ------- Fallback: ATR seguro -------
    else:
        dist = max(atr * p["atr_mult"], entry_price * p["sl_pct_min"])
        dist = min(dist, entry_price * p["sl_pct_max"])
        if side == "buy":
            stop_loss = entry_price - dist
            take_profit = entry_price + dist * rrr
        else:
            stop_loss = entry_price + dist
            take_profit = entry_price - dist * rrr

    # Segurança final: nunca deixar stop/take invertidos
    if side == "buy":
        if stop_loss >= entry_price or take_profit <= entry_price:
            stop_loss = entry_price - atr
            take_profit = entry_price + atr * rrr
    else:
        if stop_loss <= entry_price or take_profit >= entry_price:
            stop_loss = entry_price + atr
            take_profit = entry_price - atr * rrr

    if debug:
        logging.info(f"[STOP/TAKE] SL: {stop_loss:.2f} | TP: {take_profit:.2f} | Entrada: {entry_price:.2f} | Side: {side} | Contexto: {contexto}")

    return float(round(stop_loss, 2)), float(round(take_profit, 2)), tipo_stop, contexto




# --- Exemplo de uso no pipeline principal ---
if __name__ == "__main__":
    # Simule um DataFrame realista (substitua pelo seu get_market_data)
    df = pd.DataFrame({
        "ATR": np.random.uniform(60, 200, 100),
        "close": np.random.uniform(110_000, 120_000, 100),
        "BB_Upper": np.random.uniform(119_000, 121_000, 100),
        "BB_Lower": np.random.uniform(109_000, 111_000, 100),
        "volume": np.random.uniform(10, 100, 100),
        "RSI": np.random.uniform(40, 60, 100),
        "low": np.random.uniform(110_000, 115_000, 100),
        "high": np.random.uniform(115_000, 120_000, 100),
    })

    entry = 115_000
    side = "buy"
    sl, tp, tipo, contexto = calcular_stop_take_adaptativo(df, entry, side, debug=True)
    print(f"SL: {sl}, TP: {tp}, Tipo: {tipo}, Contexto: {contexto}")

