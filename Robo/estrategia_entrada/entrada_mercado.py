import logging
import pandas as pd
import numpy as np
import requests
import os
from decisao.decision_engine import avaliar_decisao_final
from estrategias.analise_sentimento import analisar_sentimento
from dotenv import load_dotenv
import sys
import time
from estrategia_entrada.score_dinamico import ajustar_score_dinamico
from feedback.feedback_trade import analisar_trades
from trade_quality.trade_classifier import (
    avaliar_decisao_trade_quality,
    carregar_dataset,
    extrair_features_trade,
    prever_probabilidade_trade,
)
from utils.trade_transition_manager import detectar_regime_mercado
from utils.indicadores import preparar_df


# O ambiente local pode informar um arquivo .env, mas no Render as variáveis
# chegam diretamente pelo ambiente do serviço. Importar este módulo nunca deve
# encerrar o worker apenas porque /app/.env não existe.
dotenv_arg = sys.argv[1] if len(sys.argv) > 1 else ""
dotenv_path = os.path.abspath(dotenv_arg) if dotenv_arg else ""

if dotenv_path and os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    print(f"ðŸ”— Arquivo .env carregado: {dotenv_path}")
else:
    if dotenv_path:
        print(f"âš ï¸ Arquivo ENV nÃ£o encontrado: {dotenv_path}; usando variáveis do ambiente")
    else:
        print("ENV por arquivo não encontrado; usando variáveis do ambiente")

symbol = os.getenv("ATIVOS", "DESCONHECIDO")
print(f"ðŸ§© Ativo em operaÃ§Ã£o: {symbol}")

    

# ConfiguraÃ§Ã£o dos Pesos para PontuaÃ§Ã£o de Entrada
CONFIRMATION_WEIGHTS = {
    "lstm_prediction": int(os.getenv("WEIGHT_LSTM", 2)),
    "sentiment_analysis": int(os.getenv("WEIGHT_SENTIMENT", 2)),
    "technical_indicators": int(os.getenv("WEIGHT_TECHNICAL", 2)),
    "liquidity_volume": int(os.getenv("WEIGHT_VOLUME", 2)),
}

ENTRY_MODES = {
    "conservador": int(os.getenv("SCORE_MINIMO_ENTRADA_CONSERVADOR", 8)),
    "moderado": int(os.getenv("SCORE_MINIMO_ENTRADA_MODERADO", 6)),
    "agressivo": int(os.getenv("SCORE_MINIMO_ENTRADA_AGRESSIVO", 4)),
}


def _obter_regras_validacao(mode_override=None):
    """
    Fonte unica de score minimo:
    1) Regime ativo (utils.config_validacao)
    2) Fallback ENV legado (ENTRY_MODES)
    """
    modo = (mode_override or os.getenv("MODO_VALIDACAO", "agressivo")).strip().lower()
    try:
        from utils.config_validacao import obter_config_validacao

        score_min, _score_pct, modo_regime = obter_config_validacao()
        return int(score_min), str(modo_regime).strip().lower()
    except Exception:
        return int(ENTRY_MODES.get(modo, 4)), modo


def _normalizar_regime(valor):
    valor = str(valor or "").strip().lower()
    if not valor:
        return "tendencia"
    if valor in {"lateral", "range", "ranging", "lateralizacao"}:
        return "lateral"
    if valor in {"caos", "choppy", "volatil", "volatilidade", "alta_volatilidade"}:
        return "caos"
    return "tendencia"


def _obter_regime_unificado(df):
    try:
        return _normalizar_regime(detectar_regime_mercado(df))
    except Exception:
        return "tendencia"


def _obter_estrategia_ativa():
    return str(
        os.getenv("ENTRY_PRIMARY_STRATEGY")
        or os.getenv("ESTRATEGIA_ATUAL")
        or os.getenv("MELHOR_ESTRATEGIA")
        or "breakout"
    ).strip().lower()


def calcular_score_dinamico(score_base, regime_unificado, estrategia, dados_feedback_recente):
    score_base = float(score_base or 0.0)
    regime_unificado = _normalizar_regime(regime_unificado)
    estrategia = str(estrategia or "breakout").strip().lower()
    feedback = dados_feedback_recente or {}

    ajuste_regime = 0.0
    ajuste_feedback = 0.0
    motivos = []

    if regime_unificado == "tendencia" and estrategia in ("breakout", "early_trend", "trend", "tendencial"):
        ajuste_regime += 0.5
        motivos.append("regime_tendencia_favorece_estrategia_tendencial")
    elif regime_unificado == "lateral" and estrategia in ("breakout", "early_trend"):
        ajuste_regime -= 0.75
        motivos.append("regime_lateral_penaliza_breakout")
    elif regime_unificado == "lateral" and estrategia in ("mean_reversion", "marketmaking", "market_making"):
        ajuste_regime += 0.25
        motivos.append("regime_lateral_favorece_estrategia_lateral")
    elif regime_unificado == "caos":
        ajuste_regime -= 0.75
        motivos.append("regime_caos_reduz_confianca")

    total_trades = int(feedback.get("total_trades") or 0)
    loss_rate = float(feedback.get("loss_rate") or 0.0)
    perdas_por_regime = feedback.get("perdas_por_regime") or {}
    perdas_por_estrategia = feedback.get("perdas_por_estrategia") or {}
    perdas_com_score_baixo = int(feedback.get("perdas_com_score_baixo") or 0)

    perdas_regime_atual = int(perdas_por_regime.get(regime_unificado, 0) or 0)
    perdas_estrategia_atual = int(perdas_por_estrategia.get(estrategia, 0) or 0)
    total_losses = int(feedback.get("losses") or 0)

    if total_trades >= 5 and loss_rate >= 0.65:
        ajuste_feedback -= 0.5
        motivos.append("feedback_loss_rate_recente_alta")

    if total_losses >= 4 and perdas_regime_atual >= 3:
        ajuste_feedback -= 0.5
        motivos.append("feedback_perdas_relevantes_no_regime_atual")

    if total_losses >= 4 and perdas_estrategia_atual >= 3:
        ajuste_feedback -= 0.5
        motivos.append("feedback_estrategia_com_perdas_recentes")

    if perdas_com_score_baixo >= 3 and score_base < 6.0:
        ajuste_feedback -= 0.25
        motivos.append("feedback_score_baixo_tem_historico_ruim")

    ajuste_total = ajuste_regime + ajuste_feedback
    ajuste_total = max(-2.0, min(1.0, ajuste_total))
    score_final = max(0.5, score_base + ajuste_total)

    return {
        "score_base": score_base,
        "ajuste_regime": round(ajuste_regime, 2),
        "ajuste_feedback": round(ajuste_feedback, 2),
        "score_final": round(score_final, 2),
        "motivos": motivos,
        "regime_unificado": regime_unificado,
        "estrategia": estrategia,
    }



def obter_previsao_next_candle(df_lstm):
    # Alguns ativos (por exemplo, AVAX) operam deliberadamente sem LSTM.
    # Nesse caso não tentar montar uma requisição com URL ausente: o restante
    # do funil deve seguir com os indicadores e seus próprios gates.
    if os.getenv("LSTM_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        logging.info("[LSTM SERVICE] desativado para este ativo; seguindo sem previsão LSTM.")
        return None

    url = os.getenv("LSTM_API_URL")  # ex: http://127.0.0.1:8505/predict

    if not url:
        logging.info("[LSTM SERVICE] sem URL configurada; seguindo sem previsão LSTM.")
        return None

    payload = {"data": df_lstm.values.tolist()}
    try:
        r = requests.post(url, json=payload, timeout=3)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None



def should_enter_market(
    df,
    mode=None,
    side="buy",
    sentimento=None,
    return_details=False,
    timeframe_label=None,
):
    """
    Calcula a pontuaÃ§Ã£o e valida se deve entrar no mercado para COMPRA (buy) ou VENDA (sell).
    Usa o sentimento passado externamente (global) para evitar mÃºltiplas chamadas Ã  API.
    """

    def _retornar(passou, score, predicted_price, sentimento_saida, detalhes=None):
        if return_details:
            return passou, score, predicted_price, sentimento_saida, (detalhes or {})
        return passou, score, predicted_price, sentimento_saida

    # ================================================================
    # ðŸš§ PROTEÃ‡ÃƒO GLOBAL - mÃ­nimo de candles para anÃ¡lise REAL
    # ================================================================
    if len(df) < 30:
        logging.warning(
            f"âš ï¸ DF muito curto ({len(df)} candles) â€” "
            "mÃ­nimo de 30 necessÃ¡rio para LSTM + indicadores estÃ¡veis. "
            "Entrada cancelada."
        )
        return _retornar(False, 0, None, "df_pequeno")
    # ================================================================

    if mode is None:
        mode = os.getenv("MODO_VALIDACAO", "agressivo").strip().lower()

    selected_strategy = os.getenv("GENESIS_STRATEGY_SELECTION", "").strip().upper().replace(" ", "_")
    strict_pullback_parity = (
        selected_strategy == "PULLBACK_TREND_FOLLOWING"
        and os.getenv("PULLBACK_BACKTEST_PARITY", "0").strip().lower()
        in ("1", "true", "yes", "on")
    )

    # ================================================================
    # ðŸ§­ Gate de tendÃªncia macro (evita lado errado em contra-tendÃªncia)
    # ================================================================
    entry_require_trend_align = (
        os.getenv("ENTRY_REQUIRE_TREND_ALIGN", "1").strip().lower() in ("1", "true", "yes")
        and not strict_pullback_parity
    )
    trend_lookback = int(os.getenv("ENTRY_TREND_LOOKBACK", "5"))
    trend_lookback = max(2, trend_lookback)
    trend_min_gap_pct = float(os.getenv("ENTRY_TREND_MIN_GAP_PCT", "0.0006"))
    trend_min_slope_pct = float(os.getenv("ENTRY_TREND_MIN_SLOPE_PCT", "0.0001"))

    ema50_series = df["EMA50"] if "EMA50" in df.columns else (df["EMA_50"] if "EMA_50" in df.columns else None)
    ema200_series = df["EMA200"] if "EMA200" in df.columns else (df["EMA_200"] if "EMA_200" in df.columns else None)
    macro_trend = "neutral"
    close_now = float(df["close"].iloc[-1]) if "close" in df.columns else 0.0
    if ema50_series is not None and ema200_series is not None:
        ema50_now = float(ema50_series.iloc[-1])
        ema200_now = float(ema200_series.iloc[-1])
        ema50_prev = float(ema50_series.iloc[-trend_lookback]) if len(ema50_series) > trend_lookback else float(ema50_series.iloc[0])
        slope = ema50_now - ema50_prev
        gap_pct = abs(ema50_now - ema200_now) / max(abs(close_now), 1e-9)
        slope_pct = abs(slope) / max(abs(close_now), 1e-9)
        trend_up = (ema50_now > ema200_now) and (slope > 0) and (gap_pct >= trend_min_gap_pct) and (slope_pct >= trend_min_slope_pct)
        trend_down = (ema50_now < ema200_now) and (slope < 0) and (gap_pct >= trend_min_gap_pct) and (slope_pct >= trend_min_slope_pct)
        if trend_up:
            macro_trend = "bullish"
        elif trend_down:
            macro_trend = "bearish"

        if entry_require_trend_align:
            if side == "buy" and not trend_up:
                logging.info(
                    f"ðŸš« BUY bloqueado por tendÃªncia macro | EMA50={ema50_now:.4f} | EMA200={ema200_now:.4f} | "
                    f"slope={slope:.6f} | gap={gap_pct:.4%}"
                )
                return _retornar(False, 0, None, "trend_macro_contraria_buy")
            if side == "sell" and not trend_down:
                logging.info(
                    f"ðŸš« SELL bloqueado por tendÃªncia macro | EMA50={ema50_now:.4f} | EMA200={ema200_now:.4f} | "
                    f"slope={slope:.6f} | gap={gap_pct:.4%}"
                )
                return _retornar(False, 0, None, "trend_macro_contraria_sell")

    # ================================================================
    # ðŸ§  SENTIMENTO (externo ou interno)
    # ================================================================
    if sentimento is None:
        from estrategias.analise_sentimento import obter_sentimento_combinado
        sentimento = obter_sentimento_combinado()
        logging.info(f"ðŸ§  Sentimento combinado detectado (interno): {sentimento}")
    else:
        logging.info(f"ðŸ§  Sentimento global recebido (externo): {sentimento}")


    # === FILTROS DE CONTEXTO ===
    ATR_PCT_MIN = float(os.getenv("ATR_PCT_MIN", 0.0008))
    ATR_PCT_MAX = float(os.getenv("ATR_PCT_MAX", 0.0040))
    VOLUME_MINIMO = float(os.getenv("VOLUME_MINIMO", 0))
    RSI_MINIMO = float(os.getenv("RSI_MINIMO", 10))
    RSI_MAXIMO = float(os.getenv("RSI_MAXIMO", 90))

    ultimo_atr = df["ATR"].iloc[-1] if "ATR" in df.columns else None
    close = df["close"].iloc[-1] if "close" in df.columns else None
    ultimo_volume = df["volume"].iloc[-1]
    ultimo_rsi = df["RSI"].iloc[-1] if "RSI" in df.columns else None

    if ultimo_atr is not None and close:
        atr_pct = ultimo_atr / close
        timeframe_text = str(timeframe_label or "desconhecido")
        logging.info(
            "[ATR][ENTRY] timeframe=%s atr=%.8f close=%.8f percentual=%.2f%% limite_min=%.2f%% limite_max=%.2f%%",
            timeframe_text,
            float(ultimo_atr),
            float(close),
            float(atr_pct * 100.0),
            float(ATR_PCT_MIN * 100.0),
            float(ATR_PCT_MAX * 100.0),
        )
        if strict_pullback_parity:
            logging.info(
                "[PULLBACK][PARIDADE] Filtro genérico ATR ignorado; "
                "ATR será usado somente no stop/alvo/trailing do setup canônico."
            )
        elif atr_pct < ATR_PCT_MIN:
            logging.info(
                "[ATR][ENTRY][BLOCK] timeframe=%s motivo=abaixo_minimo valor=%.2f%% limite=%.2f%%",
                timeframe_text,
                float(atr_pct * 100.0),
                float(ATR_PCT_MIN * 100.0),
            )
            return _retornar(False, 0, None, "atr_pct_baixo")
        if not strict_pullback_parity and atr_pct > ATR_PCT_MAX:
            logging.info(
                "[ATR][ENTRY][BLOCK] timeframe=%s motivo=acima_maximo valor=%.2f%% limite=%.2f%%",
                timeframe_text,
                float(atr_pct * 100.0),
                float(ATR_PCT_MAX * 100.0),
            )
            return _retornar(False, 0, None, "atr_pct_alto")

    if ultimo_volume < VOLUME_MINIMO:
        logging.info(f"ðŸš« Volume ({ultimo_volume:.2f}) abaixo do mÃ­nimo ({VOLUME_MINIMO}).")
        return _retornar(False, 0, None, "volume_baixo")

    if ultimo_rsi is not None:
        if not (RSI_MINIMO <= ultimo_rsi <= RSI_MAXIMO):
            logging.info(f"ðŸš« RSI ({ultimo_rsi:.2f}) fora do range ({RSI_MINIMO}-{RSI_MAXIMO}).")
            return _retornar(False, 0, None, "rsi_fora_range")

    required_score, mode = _obter_regras_validacao(mode)
    aumento_score = int(os.getenv("AUMENTO_SCORE_VOLATILIDADE", 1))
    atr_pct = ultimo_atr / close if ultimo_atr is not None and close else None
    required_score_dinamico = ajustar_score_dinamico(required_score, atr_pct, aumento_score=aumento_score)

    entry_score = 0
    ranking_pontuacao = []

    logging.info(f"ðŸ” Verificando critÃ©rios de entrada para {side.upper()}...")



    # === PrevisÃ£o LSTM ===
    # === PREPARAÃ‡ÃƒO OBRIGATÃ“RIA DO DF ===


    # Janela bruta â€” 10 candles Ã— 5 features
    df_lstm = df[['open', 'high', 'low', 'close', 'volume']].iloc[-10:].copy()

    # ValidaÃ§Ã£o Ãºnica e clara
    if df_lstm.shape != (10, 5):
        logging.warning(f"âš ï¸ Janela LSTM invÃ¡lida: {df_lstm.shape} (esperado 10,5)")
        return _retornar(False, 0, None, "lstm_shape_invalido")

    # Corrigir NaNs
    if df_lstm.isnull().any().any():
        logging.warning("âš ï¸ Janela LSTM contÃ©m NaNs â€” aplicando forward/backfill.")
        df_lstm = df_lstm.ffill().bfill()

    # Se ainda tiver NaN, aborta
    if df_lstm.isnull().any().any():
        logging.warning("âš ï¸ Janela LSTM ainda contÃ©m NaNs apÃ³s correÃ§Ã£o.")
        return _retornar(False, 0, None, "janela_nan")

    # Faz a previsÃ£o usando o serviÃ§o (novo contrato LSTM)
    lstm_result = obter_previsao_next_candle(df_lstm)

    if not lstm_result:
        logging.warning("âš ï¸ LSTM nÃ£o retornou dados vÃ¡lidos. Prosseguindo sem LSTM.")
        predicted_price = None
        delta = 0.0
        confidence = 0.0
        direction_lstm = "none"
        out_of_range = False
    else:
        # Extrair informaÃ§Ãµes do LSTM
        predicted_price = lstm_result["predicted_price"]
        delta = lstm_result["delta"]
        confidence = lstm_result["confidence"]
        direction_lstm = lstm_result["direction"]
        out_of_range = lstm_result.get("out_of_range", False)


    # === Confidence-Weighted Layer do LSTM (nao bloqueante) ===
    close_atual = float(df["close"].iloc[-1])
    atr_val = float(df["ATR"].iloc[-1]) if "ATR" in df.columns else 0.0

    if predicted_price is not None and close_atual:
        delta = float(predicted_price) - close_atual
    else:
        delta = 0.0

    signed_delta_pct = (float(delta) / max(abs(close_atual), 1e-9)) * 100.0
    delta_pct = abs(signed_delta_pct)

    mape_recent_pct = float(os.getenv("LSTM_RECENT_MAPE_PCT", "0.5"))
    volatility_pct = (atr_val / max(abs(close_atual), 1e-9)) * 100.0 if atr_val else 0.0
    if volatility_pct <= 0.0:
        closes_tail = pd.to_numeric(df["close"].tail(20), errors="coerce").dropna()
        if len(closes_tail) >= 5:
            volatility_pct = float(closes_tail.pct_change().dropna().std() * 100.0)

    base_den = max(mape_recent_pct + max(volatility_pct, 0.0), 1e-9)
    raw_conf = delta_pct / base_den
    confidence_score = raw_conf / (1.0 + raw_conf)  # 0..1

    if out_of_range:
        logging.warning("⚠️ LSTM fora do range do scaler. Reduzindo peso para 0.")
        confidence_score = 0.0

    if confidence_score < 0.2:
        peso_lstm = 0.0
    elif confidence_score < 0.5:
        peso_lstm = 0.5
    else:
        peso_lstm = 1.0

    # Protecao contra previsoes absurdas do LSTM.
    # Se a variacao prevista for muito alta, ignora o impacto no score.
    if delta_pct > 15.0:
        logging.warning(
            "[LSTM DECISION] delta_pct=%.4f > 15%%. Previsao ignorada para evitar distorcao de score.",
            float(delta_pct),
        )
        peso_lstm = 0.0
        confidence_score = 0.0

    direction_match = (
        (side == "buy" and direction_lstm == "up") or
        (side == "sell" and direction_lstm == "down")
    )
    max_lstm_impact = 1.5
    base_lstm_weight = min(float(CONFIRMATION_WEIGHTS["lstm_prediction"]), max_lstm_impact)
    score_lstm_raw = base_lstm_weight if direction_match else (
        -base_lstm_weight if direction_lstm in ("up", "down") else 0.0
    )

    # Regras de conflito LSTM x tendencia macro.
    # Em tendencia forte, o LSTM nunca deve derrubar agressivamente o lado favorecido pelo macro.
    macro_penalty_mult = 1.0
    if score_lstm_raw < 0:
        if macro_trend == "bearish" and side == "sell" and direction_lstm == "up":
            # Bearish: LSTM so reduz SELL com alta pequena (<+3%).
            # Alta muito grande (>+5%): reduzir parcialmente.
            if signed_delta_pct > 5.0:
                macro_penalty_mult = 0.5
            elif signed_delta_pct < 3.0:
                macro_penalty_mult = 1.0
            else:
                macro_penalty_mult = 0.0
        elif macro_trend == "bullish" and side == "buy" and direction_lstm == "down":
            # Bullish: LSTM so reduz BUY com queda pequena (<-3%).
            # Queda muito forte (<-5%): reduzir parcialmente.
            if signed_delta_pct < -5.0:
                macro_penalty_mult = 0.5
            elif signed_delta_pct > -3.0:
                macro_penalty_mult = 1.0
            else:
                macro_penalty_mult = 0.0

    score_lstm_raw *= macro_penalty_mult
    impacto_no_score = round(peso_lstm * score_lstm_raw, 2)
    impacto_no_score = max(-max_lstm_impact, min(max_lstm_impact, impacto_no_score))
    lstm_preve_entrada = impacto_no_score > 0

    if impacto_no_score != 0:
        entry_score += impacto_no_score
        ranking_pontuacao.append((impacto_no_score, "LSTM Ponderado"))

    logging.info(
        "[LSTM] predicted_price=%.2f | delta_pct=%.4f | confidence_score=%.4f | peso_lstm=%.2f | impacto_no_score=%.2f",
        float(predicted_price) if predicted_price is not None else 0.0,
        float(delta_pct),
        float(confidence_score),
        float(peso_lstm),
        float(impacto_no_score),
    )
    logging.info(
        "[LSTM DECISION] macro_trend=%s | predicted_price=%.2f | delta_pct=%.4f | peso_aplicado=%.2f | score_final=%.2f",
        str(macro_trend),
        float(predicted_price) if predicted_price is not None else 0.0,
        float(signed_delta_pct),
        float(impacto_no_score),
        float(entry_score),
    )
    if confidence_score < 0.2:
        logging.info("[LSTM] confidence<0.2 -> sinal ignorado (nao bloqueia sozinho).")


    # === Definir sentimento desejado por lado ===
    sentimento_desejado = "positivo" if side == "buy" else "negativo"



    # === AnÃ¡lise de Sentimento (usando parÃ¢metro externo) ===
    sentiment_score = sentimento or "neutro"
    logging.info(f"ðŸ§  Sentimento em uso: {sentiment_score}")

    if sentiment_score.lower() == "neutro":
        sentimento_ok = False
    else:
        sentimento_ok = sentiment_score.lower() == sentimento_desejado

    peso_sentimento = {"conservador": 2, "moderado": 1, "agressivo": 0.5}.get(mode, 1)

    # === PontuaÃ§Ã£o por Sentimento ===
    if sentimento_ok:
        pontos = round(CONFIRMATION_WEIGHTS["sentiment_analysis"] * peso_sentimento, 2)
        entry_score += pontos
        ranking_pontuacao.append((pontos, f"Sentimento {sentimento_desejado.capitalize()}"))
        logging.info(f"âœ… Sentimento {sentimento_desejado} â†’ +{pontos} pontos.")
    elif sentiment_score.lower() == "neutro":
        logging.info(f"âšª Sentimento neutro â†’ 0 pontos.")
    else:
        pontos = round(-CONFIRMATION_WEIGHTS["sentiment_analysis"] * peso_sentimento, 2)
        entry_score += pontos
        ranking_pontuacao.append((pontos, "Sentimento ContrÃ¡rio"))
        logging.info(f"âŒ Sentimento contrÃ¡rio â†’ {pontos} pontos.")


    # === ConvergÃªncia entre LSTM e Sentimento ===
    if peso_lstm > 0 and lstm_preve_entrada and sentimento_ok and confidence_score >= 0.5:
        entry_score += 1
        ranking_pontuacao.append((1, "ConvergÃªncia LSTM + Sentimento"))
        logging.info("ðŸ’¡ ConvergÃªncia LSTM + Sentimento +1.")
    elif peso_lstm > 0 and (lstm_preve_entrada != sentimento_ok):
        entry_score -= 1
        ranking_pontuacao.append((-1, "DivergÃªncia LSTM x Sentimento"))
        logging.warning("âš ï¸ DivergÃªncia entre LSTM e Sentimento -1.")


    # === CondiÃ§Ãµes tÃ©cnicas por lado ===
    condicoes = []

    if side == "buy":
        if df["RSI"].iloc[-1] > 50:
            condicoes.append("RSI>50")
        if df["close"].iloc[-1] > df["EMA50"].iloc[-1]:
            condicoes.append("Close>EMA50")
        if df["MACD"].iloc[-1] > df["Signal_Line"].iloc[-1]:
            condicoes.append("MACD>Signal")
    else:  # sell
        if df["RSI"].iloc[-1] < 50:
            condicoes.append("RSI<50")
        if df["close"].iloc[-1] < df["EMA50"].iloc[-1]:
            condicoes.append("Close<EMA50")
        if df["MACD"].iloc[-1] < df["Signal_Line"].iloc[-1]:
            condicoes.append("MACD<Signal")


    # === Indicadores TÃ©cnicos ===
    if len(condicoes) >= 2:
        pontos = CONFIRMATION_WEIGHTS["technical_indicators"] + 1

        entry_score += pontos
        ranking_pontuacao.append((pontos, "Indicadores TÃ©cnicos"))
        logging.info(f"âœ… Indicadores tÃ©cnicos confirmados +{pontos}.")
    else:
        logging.info(f"âŒ Indicadores tÃ©cnicos insuficientes: {condicoes}")

    # === Volume ===
    if df["volume"].iloc[-1] > df["volume"].mean():
        pontos = CONFIRMATION_WEIGHTS["liquidity_volume"]
        entry_score += pontos
        ranking_pontuacao.append((pontos, "Volume Acima da MÃ©dia"))
        logging.info(f"âœ… Volume alto +{pontos}.")
    else:
        logging.info("âŒ Volume abaixo da mÃ©dia.")

    # LSTM nunca pode bloquear sozinho uma operacao.
    score_sem_lstm = float(entry_score - impacto_no_score)
    if impacto_no_score < 0 and score_sem_lstm >= required_score_dinamico and entry_score < required_score_dinamico:
        logging.info(
            "[LSTM DECISION] Penalizacao do LSTM ignorada para evitar bloqueio isolado | sem_lstm=%.2f | com_lstm=%.2f | required=%.2f",
            float(score_sem_lstm),
            float(entry_score),
            float(required_score_dinamico),
        )
        entry_score = score_sem_lstm
        impacto_no_score = 0.0

    # === Resultado Final ===
    ranking_pontuacao.sort(reverse=True, key=lambda x: x[0])
    logging.info(f"\nðŸ† Ranking dos CritÃ©rios ({side.upper()}):")
    for pontos, criterio in ranking_pontuacao:
        logging.info(f"âž¡ï¸ {criterio}: {pontos} pontos")

    regime_unificado = _obter_regime_unificado(df)
    estrategia_ativa = _obter_estrategia_ativa()
    feedback_lookback = int(os.getenv("FEEDBACK_SCORE_LOOKBACK_TRADES", "30"))
    feedback_recente = analisar_trades(limit_recent=feedback_lookback)
    score_dinamico = calcular_score_dinamico(
        score_base=entry_score,
        regime_unificado=regime_unificado,
        estrategia=estrategia_ativa,
        dados_feedback_recente=feedback_recente,
    )
    score_final = float(score_dinamico["score_final"])
    trade_quality_features = extrair_features_trade(
        df,
        {
            "rsi": ultimo_rsi,
            "atr": ultimo_atr,
            "sentimento": sentiment_score,
            "estrategia": estrategia_ativa,
            "tendencia": regime_unificado,
            "regime": regime_unificado,
            "score_entrada": score_final,
            "decisao_lstm": direction_lstm,
        },
    )
    trade_quality_prob, trade_quality_status = prever_probabilidade_trade(trade_quality_features)
    try:
        trade_quality_samples = len(carregar_dataset())
    except Exception:
        trade_quality_samples = 0
    trade_quality_result = avaliar_decisao_trade_quality(
        probabilidade=trade_quality_prob,
        score_heuristico=score_final,
        metricas=feedback_recente,
        features=trade_quality_features,
        modelo_status=trade_quality_status,
        sample_count=trade_quality_samples,
    )

    logging.info(
        "ðŸŽ›ï¸ Score dinÃ¢mico | lado=%s | estrategia=%s | regime=%s | base=%.2f | ajuste_regime=%.2f | ajuste_feedback=%.2f | final=%.2f | motivos=%s",
        side.upper(),
        estrategia_ativa,
        regime_unificado,
        float(score_dinamico["score_base"]),
        float(score_dinamico["ajuste_regime"]),
        float(score_dinamico["ajuste_feedback"]),
        float(score_final),
        score_dinamico.get("motivos", []),
    )
    logging.info(
        "ðŸ§ª Trade Quality | lado=%s | estrategia=%s | prob=%.4f | decisao=%s | fator_lote=%.2f",
        side.upper(),
        estrategia_ativa,
        float(trade_quality_prob),
        trade_quality_result.get("decisao"),
        float(trade_quality_result.get("lot_factor") or 0.0),
    )
    logging.info(f"ðŸ”¢ PontuaÃ§Ã£o final: {score_final}/{required_score_dinamico} "
                 f"(Modo: {mode.upper()} | Lado: {side.upper()})")

    resultado_decisao = avaliar_decisao_final(
        score_base=entry_score,
        score_final=score_final,
        required_score=required_score_dinamico,
        breakout_confirmado=False,
        regime=regime_unificado,
        setup_ok=True,
        contexto_ok=True,
    )
    logging.info(
        "Decision Engine -> strength=%s | motivo=%s",
        resultado_decisao.get("signal_strength"),
        resultado_decisao.get("motivo"),
    )
    detalhes_saida = dict(score_dinamico)
    detalhes_saida["trade_quality_probabilidade"] = round(float(trade_quality_prob), 4)
    detalhes_saida["trade_quality_threshold"] = trade_quality_result.get("threshold")
    detalhes_saida["trade_quality_score_hibrido"] = trade_quality_result.get("score_hibrido")
    detalhes_saida["trade_quality_decisao"] = trade_quality_result.get("decisao")
    detalhes_saida["trade_quality_motivo"] = trade_quality_result.get("motivo")
    detalhes_saida["trade_quality_modelo_status"] = trade_quality_status
    detalhes_saida["trade_quality_sample_count"] = trade_quality_samples
    detalhes_saida["trade_quality_lot_factor"] = float(trade_quality_result.get("lot_factor") or 0.0)
    detalhes_saida["trade_quality_features"] = trade_quality_features
    passou = bool(resultado_decisao.get("permitido"))
    if trade_quality_result.get("decisao") == "bloqueado":
        logging.info(
            "ðŸš« Entrada bloqueada pelo Trade Quality | lado=%s | prob=%.4f | motivo=%s",
            side.upper(),
            float(trade_quality_prob),
            trade_quality_result.get("motivo"),
        )
        passou = False
    return _retornar(passou, score_final, predicted_price, sentiment_score, detalhes_saida)


def avaliar_ambos_os_lados(df, mode=None):
    from estrategias.analise_sentimento import obter_sentimento_combinado
    sentimento_global = obter_sentimento_combinado()

    logging.info(f"ðŸ§­ Usando sentimento global Ãºnico para avaliaÃ§Ã£o BUY/SELL: {sentimento_global}")
    logging.info("âš–ï¸ Avaliando simultaneamente COMPRA e VENDA...")

    sent_buy_eff = _sentimento_por_lado(sentimento_global, "buy")
    sent_sell_eff = _sentimento_por_lado(sentimento_global, "sell")
    passou_buy, score_buy, pred_buy, sent_buy, details_buy = should_enter_market(
        df, mode, side="buy", sentimento=sent_buy_eff, return_details=True
    )
    passou_sell, score_sell, pred_sell, sent_sell, details_sell = should_enter_market(
        df, mode, side="sell", sentimento=sent_sell_eff, return_details=True
    )

    resultado = {
        "buy": {
            "passou": passou_buy,
            "score": score_buy,
            "score_base": details_buy.get("score_base"),
            "score_final": details_buy.get("score_final"),
            "motivos_score": details_buy.get("motivos", []),
            "regime": details_buy.get("regime_unificado"),
            "pred": pred_buy,
            "sent": sent_buy,
        },
        "sell": {
            "passou": passou_sell,
            "score": score_sell,
            "score_base": details_sell.get("score_base"),
            "score_final": details_sell.get("score_final"),
            "motivos_score": details_sell.get("motivos", []),
            "regime": details_sell.get("regime_unificado"),
            "pred": pred_sell,
            "sent": sent_sell,
        }
    }

    logging.info(f"ðŸŸ¢ BUY â†’ Score={score_buy} | Sentimento={sent_buy} | {'âœ… OK' if passou_buy else 'âŒ Reprovado'}")
    logging.info(f"ðŸ”´ SELL â†’ Score={score_sell} | Sentimento={sent_sell} | {'âœ… OK' if passou_sell else 'âŒ Reprovado'}")

    if score_buy > score_sell:
        logging.info(f"ðŸ DireÃ§Ã£o com maior score: BUY ({score_buy} > {score_sell})")
    elif score_sell > score_buy:
        logging.info(f"ðŸ DireÃ§Ã£o com maior score: SELL ({score_sell} > {score_buy})")
    else:
        logging.info(f"âš–ï¸ Empate entre BUY e SELL ({score_buy})")

    return resultado



from datetime import datetime, timedelta

_cache_sentimento = {"valor": None, "timestamp": None}

def obter_sentimento_cacheado():
    global _cache_sentimento
    agora = datetime.utcnow()
    if _cache_sentimento["valor"] and (agora - _cache_sentimento["timestamp"]) < timedelta(minutes=1):
        return _cache_sentimento["valor"]
    from estrategias.analise_sentimento import obter_sentimento_combinado
    valor = obter_sentimento_combinado()
    _cache_sentimento = {"valor": valor, "timestamp": agora}
    return valor


def _sentimento_por_lado(sentimento_global, side):
    """
    Evita viesar BUY/SELL quando o sentimento global Ã© unidirecional.
    """
    s = (sentimento_global or "").strip().lower()
    side = (side or "").strip().lower()
    if side == "sell" and s == "positivo":
        return "neutro"
    if side == "buy" and s == "negativo":
        return "neutro"
    return sentimento_global


def validar_entrada_com_score_duplo(df, mode=None, timeframe_label=None):
    """
    Avalia compra e venda simultaneamente.
    Mesmo que um lado atinja o score mÃ­nimo primeiro,
    ambos sÃ£o avaliados e logados integralmente.
    Caso nenhum atinja, o sistema aguarda nova oportunidade.
    """
    logging.info("âš–ï¸ Avaliando critÃ©rios de entrada (BUY e SELL) simultaneamente...")

    # âœ… Sentimento global Ãºnico
    from estrategias.analise_sentimento import obter_sentimento_combinado
    sentimento_global = obter_sentimento_combinado()
    logging.info(f"ðŸ§  Sentimento global para validaÃ§Ã£o: {sentimento_global}")

    # âœ… AvaliaÃ§Ã£o dos dois lados com o mesmo sentimento
    sent_buy_eff = _sentimento_por_lado(sentimento_global, "buy")
    sent_sell_eff = _sentimento_por_lado(sentimento_global, "sell")
    passou_buy, score_buy, pred_buy, sent_buy, details_buy = should_enter_market(
        df,
        mode,
        side="buy",
        sentimento=sent_buy_eff,
        return_details=True,
        timeframe_label=timeframe_label,
    )
    passou_sell, score_sell, pred_sell, sent_sell, details_sell = should_enter_market(
        df,
        mode,
        side="sell",
        sentimento=sent_sell_eff,
        return_details=True,
        timeframe_label=timeframe_label,
    )

    required_score, mode = _obter_regras_validacao(mode)

    # === Logs de comparaÃ§Ã£o ===
    logging.info("ðŸ“Š ===== RESULTADOS DETALHADOS =====")
    logging.info(f"ðŸŸ¢ BUY  â†’ Score={score_buy} | Pred={pred_buy} | Sent={sent_buy} | {'âœ… Aprovado' if passou_buy else 'âŒ Reprovado'}")
    logging.info(f"ðŸ”´ SELL â†’ Score={score_sell} | Pred={pred_sell} | Sent={sent_sell} | {'âœ… Aprovado' if passou_sell else 'âŒ Reprovado'}")
    logging.info("ðŸ“Š ================================")
    logging.info(f"ðŸ§® Score mÃ­nimo exigido: {required_score}")

    # === LÃ³gica de liberaÃ§Ã£o ===
    if passou_buy or passou_sell:
        logging.info("ðŸš€ Pelo menos um lado atingiu o score mÃ­nimo. Ambos foram avaliados integralmente.")
        
        # Determinar qual lado tem maior score
        if score_buy > score_sell:
            lado_aprovado = "buy"
        elif score_sell > score_buy:
            lado_aprovado = "sell"
        else:
            lado_aprovado = "ambos"

        logging.info(f"ðŸ DireÃ§Ã£o com maior score: {lado_aprovado.upper()} (BUY={score_buy} | SELL={score_sell})")
        
        return True, lado_aprovado, {
            "buy": {
                "score": score_buy,
                "score_base": details_buy.get("score_base"),
                "score_final": details_buy.get("score_final"),
                "motivos_score": details_buy.get("motivos", []),
                "regime": details_buy.get("regime_unificado"),
                "trade_quality_probabilidade": details_buy.get("trade_quality_probabilidade"),
                "trade_quality_threshold": details_buy.get("trade_quality_threshold"),
                "trade_quality_score_hibrido": details_buy.get("trade_quality_score_hibrido"),
                "trade_quality_decisao": details_buy.get("trade_quality_decisao"),
                "trade_quality_modelo_status": details_buy.get("trade_quality_modelo_status"),
                "trade_quality_lot_factor": details_buy.get("trade_quality_lot_factor"),
                "sentimento": sent_buy,
                "pred": pred_buy,
            },
            "sell": {
                "score": score_sell,
                "score_base": details_sell.get("score_base"),
                "score_final": details_sell.get("score_final"),
                "motivos_score": details_sell.get("motivos", []),
                "regime": details_sell.get("regime_unificado"),
                "trade_quality_probabilidade": details_sell.get("trade_quality_probabilidade"),
                "trade_quality_threshold": details_sell.get("trade_quality_threshold"),
                "trade_quality_score_hibrido": details_sell.get("trade_quality_score_hibrido"),
                "trade_quality_decisao": details_sell.get("trade_quality_decisao"),
                "trade_quality_modelo_status": details_sell.get("trade_quality_modelo_status"),
                "trade_quality_lot_factor": details_sell.get("trade_quality_lot_factor"),
                "sentimento": sent_sell,
                "pred": pred_sell,
            }
        }

    else:
        logging.info("â¸ Nenhum lado atingiu o score mÃ­nimo. Aguardando nova oportunidade...")
        logging.info(f"ðŸ§¾ BUY Score={score_buy} | SELL Score={score_sell}")
        return False, None, {
            "buy": {
                "score": score_buy,
                "score_base": details_buy.get("score_base"),
                "score_final": details_buy.get("score_final"),
                "motivos_score": details_buy.get("motivos", []),
                "regime": details_buy.get("regime_unificado"),
                "trade_quality_probabilidade": details_buy.get("trade_quality_probabilidade"),
                "trade_quality_threshold": details_buy.get("trade_quality_threshold"),
                "trade_quality_score_hibrido": details_buy.get("trade_quality_score_hibrido"),
                "trade_quality_decisao": details_buy.get("trade_quality_decisao"),
                "trade_quality_modelo_status": details_buy.get("trade_quality_modelo_status"),
                "trade_quality_lot_factor": details_buy.get("trade_quality_lot_factor"),
                "sentimento": sent_buy,
                "pred": pred_buy,
            },
            "sell": {
                "score": score_sell,
                "score_base": details_sell.get("score_base"),
                "score_final": details_sell.get("score_final"),
                "motivos_score": details_sell.get("motivos", []),
                "regime": details_sell.get("regime_unificado"),
                "trade_quality_probabilidade": details_sell.get("trade_quality_probabilidade"),
                "trade_quality_threshold": details_sell.get("trade_quality_threshold"),
                "trade_quality_score_hibrido": details_sell.get("trade_quality_score_hibrido"),
                "trade_quality_decisao": details_sell.get("trade_quality_decisao"),
                "trade_quality_modelo_status": details_sell.get("trade_quality_modelo_status"),
                "trade_quality_lot_factor": details_sell.get("trade_quality_lot_factor"),
                "sentimento": sent_sell,
                "pred": pred_sell,
            }
        }



def checar_oportunidade_entrada(df, mode=None, timeframe_label=None):
    """
    Avalia compra e venda simultaneamente e decide se deve prosseguir.
    Retorna: prosseguir (bool), lado_aprovado (str), resultados (dict)
    """
    logging.info("âš–ï¸ Iniciando checagem de oportunidade de entrada...")

    prosseguir, lado, resultados = validar_entrada_com_score_duplo(
        df, mode, timeframe_label=timeframe_label
    )

    logging.info("ðŸ“Š Resumo Final de Oportunidade de Entrada:")
    logging.info(f"BUY  â†’ {resultados['buy']}")
    logging.info(f"SELL â†’ {resultados['sell']}")

    if prosseguir:
        logging.info(f"âœ… PrÃ³ximo passo liberado: {lado.upper()}")
    else:
        logging.info("â¸ Nenhum lado atingiu o score mÃ­nimo. Aguardando nova oportunidade...")

    return prosseguir, lado, resultados

