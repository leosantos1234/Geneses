import csv
import glob
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from api_handlers.market_data import get_market_data_bybit, initialize_session
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from utils.indicadores import calculate_indicators

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None


logger = logging.getLogger(__name__)

# Limite temporário de validação durante a formação da base reconciliada.
# A base atual possui 170 amostras válidas; manteremos as demais travas de
# setup, risco e tamanho da ordem enquanto coletamos novas operações.
MIN_TRAIN_SAMPLES = 170
MIN_CONFIDENCE_SAMPLES = 300
RETRAIN_EVERY_N_TRADES = 50
MIN_BUCKET_TRADES = 10
MAX_BRIER_SCORE = 0.25
MIN_MONOTONIC_CORRELATION = 0.3

_LOCK = threading.Lock()
_BASE_DIR = Path(__file__).resolve().parent
_DB_PATH = _BASE_DIR / "trade_quality.db"
_CSV_PATH = _BASE_DIR / "trade_quality_dataset.csv"
_MODEL_PATH = _BASE_DIR / "trade_quality_model.joblib"
_LOG_PATH = _BASE_DIR / "trade_quality_predictions.jsonl"
_VALIDATION_REPORT_PATH = _BASE_DIR / "validation_report.json"
_CALIBRATION_HISTORY_PATH = _BASE_DIR / "calibration_history.json"
_FINAL_REPORT_PATH = _BASE_DIR / "relatorio_final.json"
_MODEL_CONTEXT_PATH = _BASE_DIR / "model_context.pkl"
_TRAINING_REPORT_CONTEXT_PATH = _BASE_DIR / "training_report_context.json"
_DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:5432/genesis_trading"


def _database_url() -> str:
    return str(os.getenv("GENESIS_DATABASE_URL", "")).strip() or _DEFAULT_DATABASE_URL


def _using_postgres() -> bool:
    return bool(_database_url())


def _quality_connect():
    if _using_postgres():
        if psycopg is None:
            raise RuntimeError("GENESIS_DATABASE_URL configurada, mas psycopg não está instalado.")
        return psycopg.connect(_database_url(), connect_timeout=5)
    return sqlite3.connect(_DB_PATH)


def _quality_sql(query: str) -> str:
    return query.replace("?", "%s") if _using_postgres() else query


def _main_trade_connect():
    if _using_postgres():
        if psycopg is None:
            raise RuntimeError("GENESIS_DATABASE_URL configurada, mas psycopg não está instalado.")
        return psycopg.connect(_database_url(), connect_timeout=5, row_factory=dict_row)
    conn = sqlite3.connect(Path(__file__).resolve().parents[1] / "trades.db")
    conn.row_factory = sqlite3.Row
    return conn

_FEATURE_COLUMNS = [
    "rsi",
    "atr",
    "volume_ratio",
    "lstm_signal",
    "sentimento",
    "estrategia",
    "tendencia",
    "volatilidade",
    "score_entrada",
    "distancia_resistencia",
    "distancia_suporte",
    "inclinacao_ema50",
    "inclinacao_ema200",
    "atr_normalizado",
    "posicao_bollinger",
    "candles_desde_breakout",
    "confluencia_sinais",
]

_STORAGE_COLUMNS = _FEATURE_COLUMNS + [
    "resultado",
    "timestamp",
    "lucro",
    "prejuizo",
    "pnl_abs",
    "drawdown",
    "probabilidade",
    "flag_filtrado",
    "teria_bloqueado",
    "modelo_status",
]

_CONTEXT_FEATURE_COLUMNS = [
    "ativo",
    "lado",
    "strategy",
    "decision_source",
    "preco_entrada",
    "quantidade",
    "distancia_resistencia",
    "distancia_suporte",
    "ema20",
    "ema50",
    "ema200",
    "inclinacao_ema20",
    "distancia_preco_ema50",
    "atr_normalizado",
    "largura_bollinger",
    "posicao_bollinger",
    "rsi",
    "volume_ratio",
    "candles_desde_breakout",
    "corpo_candle_pct",
    "pavio_superior_pct",
    "pavio_inferior_pct",
    "hora_entrada",
    "dia_semana_entrada",
]

_DECISION_FEATURE_COLUMNS = [
    "score_entrada_decisao",
    "score_normalizado",
    "decisao_final",
    "intensidade_sinal",
    "qtd_agentes_favor",
    "qtd_agentes_contra",
    "consenso_agentes",
    "divergencia_agentes",
    "rsi_sinal",
    "tendencia_sinal",
    "conflito_sinais",
    "tipo_estrategia",
    "breakout_valido",
    "distancia_entrada_rompimento",
    "atraso_entrada",
    "win_rate_ultimos_10",
    "win_rate_ultimos_20",
    "resultado_ultimo_trade",
    "sequencia_loss",
    "rr_ratio",
    "stop_distance_pct",
    "take_distance_pct",
    "tipo_ordem",
    "slippage_estimado",
]

_TRAINING_FEATURE_COLUMNS = _CONTEXT_FEATURE_COLUMNS + _DECISION_FEATURE_COLUMNS


def _to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "None"):
            return default
        return float(value)
    except Exception:
        return default


def _safe_div(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    num = _to_float(numerator, None)
    den = _to_float(denominator, None)
    if num is None or den in (None, 0.0):
        return default
    try:
        return float(num / den)
    except Exception:
        return default


def _normalize_strategy(value: Any) -> str:
    return str(value or "breakout").strip().lower() or "breakout"


def _normalize_trend(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"bullish", "alta", "buy", "up", "tendencia", "trend", "tendência"}:
        return "tendencia"
    if raw in {"lateral", "range", "ranging", "lateralizacao"}:
        return "lateral"
    if raw in {"caos", "choppy", "volatil", "volatilidade", "volatile"}:
        return "caos"
    return raw or "tendencia"


def _normalize_binary_target(value: Any) -> int:
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"win", "1", "true", "positivo"}:
            return 1
        return 0
    return 1 if int(value or 0) == 1 else 0


def _normalize_lstm_signal(value: Any) -> int:
    if isinstance(value, (int, float)):
        if float(value) > 0:
            return 1
        if float(value) < 0:
            return -1
        return 0

    raw = str(value or "").strip().lower()
    if raw in {"buy", "up", "alta", "positivo", "bullish"}:
        return 1
    if raw in {"sell", "down", "baixa", "negativo", "bearish"}:
        return -1
    return 0


def _normalize_sentimento(value: Any) -> int:
    if isinstance(value, (int, float)):
        if float(value) > 0:
            return 1
        if float(value) < 0:
            return -1
        return 0

    raw = str(value or "").strip().lower()
    if raw in {"positivo", "bullish", "buy", "alta"}:
        return 1
    if raw in {"negativo", "bearish", "sell", "baixa"}:
        return -1
    return 0


def _safe_timestamp(value: Any | None = None) -> str:
    if value not in (None, ""):
        return str(value)
    return datetime.utcnow().isoformat()


def _safe_json_dump(path: Path, payload: Any) -> None:
    try:
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Falha ao salvar JSON em %s: %s", path, e)


def _safe_json_load(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Falha ao ler JSON em %s: %s", path, e)
        return default


def _ensure_storage() -> None:
    os.makedirs(_BASE_DIR, exist_ok=True)
    if _using_postgres():
        with _quality_connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_quality_samples (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TEXT,
                    rsi REAL,
                    atr REAL,
                    volume_ratio REAL,
                    lstm_signal INTEGER,
                    sentimento INTEGER,
                    estrategia TEXT,
                    tendencia TEXT,
                    volatilidade REAL,
                    score_entrada REAL,
                    distancia_resistencia REAL,
                    distancia_suporte REAL,
                    inclinacao_ema50 REAL,
                    inclinacao_ema200 REAL,
                    atr_normalizado REAL,
                    posicao_bollinger REAL,
                    candles_desde_breakout REAL,
                    confluencia_sinais INTEGER,
                    resultado INTEGER,
                    lucro REAL,
                    prejuizo REAL,
                    pnl_abs REAL,
                    drawdown REAL,
                    probabilidade REAL,
                    flag_filtrado INTEGER,
                    teria_bloqueado INTEGER,
                    modelo_status TEXT
                )
                """
            )
            conn.commit()
        return

    with sqlite3.connect(_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_quality_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                rsi REAL,
                atr REAL,
                volume_ratio REAL,
                lstm_signal INTEGER,
                sentimento INTEGER,
                estrategia TEXT,
                tendencia TEXT,
                volatilidade REAL,
                score_entrada REAL,
                distancia_resistencia REAL,
                distancia_suporte REAL,
                inclinacao_ema50 REAL,
                inclinacao_ema200 REAL,
                atr_normalizado REAL,
                posicao_bollinger REAL,
                candles_desde_breakout REAL,
                confluencia_sinais INTEGER,
                resultado INTEGER,
                lucro REAL,
                prejuizo REAL,
                pnl_abs REAL,
                drawdown REAL,
                probabilidade REAL,
                flag_filtrado INTEGER,
                teria_bloqueado INTEGER,
                modelo_status TEXT
            )
            """
        )
        existing_columns = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(trade_quality_samples)").fetchall()
        }
        expected_columns = {
            "lucro": "REAL",
            "prejuizo": "REAL",
            "pnl_abs": "REAL",
            "drawdown": "REAL",
            "probabilidade": "REAL",
            "flag_filtrado": "INTEGER",
            "teria_bloqueado": "INTEGER",
            "modelo_status": "TEXT",
        }
        for col, sql_type in expected_columns.items():
            if col not in existing_columns:
                cursor.execute(f"ALTER TABLE trade_quality_samples ADD COLUMN {col} {sql_type}")
        conn.commit()


def _append_csv(row: dict) -> None:
    file_exists = _CSV_PATH.exists()
    with open(_CSV_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_STORAGE_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({key: row.get(key) for key in _STORAGE_COLUMNS})


def _write_prediction_log(payload: dict) -> None:
    try:
        os.makedirs(_BASE_DIR, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Falha ao escrever log do Trade Quality Score: %s", e)


def carregar_dataset() -> pd.DataFrame:
    _ensure_storage()
    try:
        with _quality_connect() as conn:
            # Use the driver's cursor directly. This avoids pandas' warning
            # about an untested psycopg DBAPI connection while preserving
            # compatibility with the SQLite fallback.
            cursor = conn.cursor()
            try:
                cursor.execute(_quality_sql("SELECT * FROM trade_quality_samples ORDER BY id ASC"))
                rows = cursor.fetchall()
                columns = [item[0] for item in (cursor.description or [])]
            finally:
                cursor.close()
            df = pd.DataFrame.from_records(rows, columns=columns)
        if not df.empty:
            return df
    except Exception as e:
        logger.warning("Falha ao ler samples SQLite do Trade Quality: %s", e)

    if _CSV_PATH.exists():
        try:
            return pd.read_csv(_CSV_PATH)
        except Exception as e:
            logger.warning("Falha ao ler CSV fallback do Trade Quality: %s", e)

    return pd.DataFrame(columns=_STORAGE_COLUMNS)


def _prepare_training_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    base = df.copy()
    for col in _FEATURE_COLUMNS:
        if col not in base.columns:
            base[col] = None

    numeric_defaults = {
        "rsi": 50.0,
        "atr": 0.0,
        "volume_ratio": 1.0,
        "lstm_signal": 0,
        "sentimento": 0,
        "volatilidade": 0.0,
        "score_entrada": 0.0,
        "distancia_resistencia": 0.0,
        "distancia_suporte": 0.0,
        "inclinacao_ema50": 0.0,
        "inclinacao_ema200": 0.0,
        "atr_normalizado": 0.0,
        "posicao_bollinger": 0.5,
        "candles_desde_breakout": 0.0,
        "confluencia_sinais": 0,
    }
    for col, default in numeric_defaults.items():
        base[col] = pd.to_numeric(base[col], errors="coerce").fillna(default)

    base["lstm_signal"] = base["lstm_signal"].astype(int)
    base["sentimento"] = base["sentimento"].astype(int)
    base["confluencia_sinais"] = base["confluencia_sinais"].astype(int)
    base["estrategia"] = base["estrategia"].fillna("breakout").astype(str).str.lower()
    base["tendencia"] = base["tendencia"].fillna("tendencia").astype(str).str.lower()
    base["resultado"] = pd.to_numeric(base.get("resultado"), errors="coerce").fillna(0).astype(int)

    X = pd.get_dummies(base[_FEATURE_COLUMNS], columns=["estrategia", "tendencia"], dummy_na=False)
    y = base["resultado"]
    return X, y


def treinar_modelo(dataset: pd.DataFrame | None = None, force: bool = False) -> dict:
    df = dataset.copy() if isinstance(dataset, pd.DataFrame) else carregar_dataset()
    if len(df) < MIN_TRAIN_SAMPLES:
        return {
            "trained": False,
            "motivo": f"amostras_insuficientes_{len(df)}<{MIN_TRAIN_SAMPLES}",
            "samples": len(df),
        }

    if not force and _MODEL_PATH.exists():
        try:
            bundle = joblib.load(_MODEL_PATH)
            if int(bundle.get("sample_count") or 0) >= len(df):
                return {
                    "trained": True,
                    "motivo": "modelo_existente_atual",
                    "samples": len(df),
                }
        except Exception:
            pass

    X, y = _prepare_training_frame(df)
    if y.nunique() < 2:
        return {
            "trained": False,
            "motivo": "target_sem_variacao",
            "samples": len(df),
        }

    model = RandomForestClassifier(
        n_estimators=max(int(os.getenv("TRADE_QUALITY_RF_ESTIMATORS", "120") or 120), 20),
        max_depth=max(int(os.getenv("TRADE_QUALITY_RF_MAX_DEPTH", "6") or 6), 2),
        min_samples_leaf=max(int(os.getenv("TRADE_QUALITY_RF_MIN_LEAF", "3") or 3), 1),
        random_state=42,
    )
    model.fit(X, y)

    feature_means = {}
    for col in _FEATURE_COLUMNS:
        if col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
            feature_means[col] = float(pd.to_numeric(df[col], errors="coerce").fillna(0).mean())

    bundle = {
        "model": model,
        "feature_columns": list(X.columns),
        "trained_at": datetime.utcnow().isoformat(),
        "sample_count": len(df),
        "positive_rate": float(y.mean()) if len(y) else 0.0,
        "training_profile": {
            "feature_means": feature_means,
            "win_rate": float(y.mean()) if len(y) else 0.0,
            "samples": int(len(df)),
        },
    }
    joblib.dump(bundle, _MODEL_PATH)
    return {
        "trained": True,
        "motivo": "modelo_treinado",
        "samples": len(df),
    }


def carregar_modelo() -> dict | None:
    if not _MODEL_PATH.exists():
        return None
    try:
        bundle = joblib.load(_MODEL_PATH)
        if not isinstance(bundle, dict):
            return None
        return bundle
    except Exception as e:
        logger.warning("Falha ao carregar modelo do Trade Quality: %s", e)
        return None


def _get_col(last, *names, default=None):
    if last is None:
        return default
    for name in names:
        try:
            if name in last.index:
                value = last.get(name)
                if value not in (None, ""):
                    return value
        except Exception:
            continue
    return default


def extrair_features_trade(df, contexto):
    contexto = dict(contexto or {})
    last = df.iloc[-1] if df is not None and len(df) else None
    prev = df.iloc[-2] if df is not None and len(df) > 1 else None

    close_value = _to_float(contexto.get("close"), None)
    if close_value is None:
        close_value = _to_float(_get_col(last, "close", "Close"), 0.0) or 0.0

    volume_ratio = 1.0
    if df is not None and len(df) and "volume" in df.columns:
        media_volume = pd.to_numeric(df["volume"].tail(20), errors="coerce").dropna().mean()
        volume_atual = _to_float(_get_col(last, "volume", "VOLUME"), 0.0) or 0.0
        if media_volume and media_volume > 0:
            volume_ratio = float(volume_atual / media_volume)

    atr_value = _to_float(contexto.get("atr"), None)
    if atr_value is None:
        atr_value = _to_float(_get_col(last, "ATR", "atr"), 0.0)
    atr_value = atr_value or 0.0

    volatilidade = _to_float(contexto.get("volatilidade"), None)
    if volatilidade is None:
        volatilidade = float(atr_value / close_value) if close_value > 0 else 0.0

    resistencia = _to_float(contexto.get("resistencia"), None)
    if resistencia is None and df is not None and len(df):
        resistencia = _to_float(pd.to_numeric(df["high"].tail(20), errors="coerce").max(), None) if "high" in df.columns else None

    suporte = _to_float(contexto.get("suporte"), None)
    if suporte is None and df is not None and len(df):
        suporte = _to_float(pd.to_numeric(df["low"].tail(20), errors="coerce").min(), None) if "low" in df.columns else None

    ema50_now = _to_float(contexto.get("ema50"), None)
    if ema50_now is None:
        ema50_now = _to_float(_get_col(last, "EMA50", "EMA_50"), None)
    ema50_prev = _to_float(contexto.get("ema50_anterior"), None)
    if ema50_prev is None:
        ema50_prev = _to_float(_get_col(prev, "EMA50", "EMA_50"), ema50_now)

    ema200_now = _to_float(contexto.get("ema200"), None)
    if ema200_now is None:
        ema200_now = _to_float(_get_col(last, "EMA200", "EMA_200"), None)
    ema200_prev = _to_float(contexto.get("ema200_anterior"), None)
    if ema200_prev is None:
        ema200_prev = _to_float(_get_col(prev, "EMA200", "EMA_200"), ema200_now)

    boll_upper = _to_float(contexto.get("boll_upper"), None)
    if boll_upper is None:
        boll_upper = _to_float(_get_col(last, "BB_upper", "boll_upper", "BOLL_UPPER"), None)
    boll_lower = _to_float(contexto.get("boll_lower"), None)
    if boll_lower is None:
        boll_lower = _to_float(_get_col(last, "BB_lower", "boll_lower", "BOLL_LOWER"), None)
    boll_denom = (boll_upper - boll_lower) if (boll_upper is not None and boll_lower is not None) else None

    lstm_signal = _normalize_lstm_signal(contexto.get("lstm_signal") or contexto.get("decisao_lstm"))
    sentimento = _normalize_sentimento(contexto.get("sentimento"))

    features = {
        "rsi": _to_float(contexto.get("rsi"), None),
        "atr": atr_value,
        "volume_ratio": volume_ratio,
        "lstm_signal": lstm_signal,
        "sentimento": sentimento,
        "estrategia": _normalize_strategy(contexto.get("estrategia") or contexto.get("strategy")),
        "tendencia": _normalize_trend(contexto.get("tendencia") or contexto.get("regime")),
        "volatilidade": volatilidade,
        "score_entrada": _to_float(contexto.get("score_entrada") or contexto.get("score_final") or contexto.get("score"), 0.0) or 0.0,
        "distancia_resistencia": ((resistencia - close_value) / close_value) if (resistencia is not None and close_value > 0) else 0.0,
        "distancia_suporte": ((close_value - suporte) / close_value) if (suporte is not None and close_value > 0) else 0.0,
        "inclinacao_ema50": ((ema50_now or 0.0) - (ema50_prev or 0.0)) if ema50_now is not None else 0.0,
        "inclinacao_ema200": ((ema200_now or 0.0) - (ema200_prev or 0.0)) if ema200_now is not None else 0.0,
        "atr_normalizado": float(atr_value / close_value) if close_value > 0 else 0.0,
        "posicao_bollinger": ((close_value - boll_lower) / boll_denom) if (boll_denom not in (None, 0.0) and boll_lower is not None) else 0.5,
        "candles_desde_breakout": _to_float(contexto.get("candles_desde_breakout"), 0.0) or 0.0,
        "confluencia_sinais": int(lstm_signal == sentimento) if lstm_signal != 0 and sentimento != 0 else 0,
    }

    if features["rsi"] is None:
        features["rsi"] = _to_float(_get_col(last, "RSI", "rsi"), 50.0)
    if features["rsi"] is None:
        features["rsi"] = 50.0

    return features


def extrair_features_contexto(df, preco_entrada=None):
    df_ctx = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    if df_ctx.empty:
        return {}

    required = ["open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df_ctx.columns:
            df_ctx[col] = np.nan
        df_ctx[col] = pd.to_numeric(df_ctx[col], errors="coerce")

    df_ctx = df_ctx.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if df_ctx.empty:
        return {}

    close_series = df_ctx["close"].astype(float)
    high_series = df_ctx["high"].astype(float)
    low_series = df_ctx["low"].astype(float)
    open_series = df_ctx["open"].astype(float)
    volume_series = pd.to_numeric(df_ctx["volume"], errors="coerce").fillna(0.0).astype(float)

    entry_price = _to_float(preco_entrada, None)
    if entry_price is None or entry_price <= 0:
        entry_price = _to_float(close_series.iloc[-1], 0.0) or 0.0

    enriched = calculate_indicators(df_ctx.copy())
    last = enriched.iloc[-1]
    prev = enriched.iloc[-2] if len(enriched) > 1 else last

    resistencia_local = float(high_series.tail(20).max()) if len(high_series) else 0.0
    suporte_local = float(low_series.tail(20).min()) if len(low_series) else 0.0

    ema20_series = close_series.ewm(span=20, adjust=False).mean()
    ema50_series = close_series.ewm(span=50, adjust=False).mean()
    ema200_series = close_series.ewm(span=200, adjust=False).mean()
    ema20 = float(ema20_series.iloc[-1]) if len(ema20_series) else 0.0
    ema50 = float(ema50_series.iloc[-1]) if len(ema50_series) else 0.0
    ema200 = float(ema200_series.iloc[-1]) if len(ema200_series) else 0.0
    ema20_prev = float(ema20_series.iloc[-2]) if len(ema20_series) > 1 else ema20

    atr = _to_float(last.get("ATR"), 0.0) or 0.0
    boll_upper = _to_float(last.get("Bollinger_Upper"), None)
    boll_lower = _to_float(last.get("Bollinger_Lower"), None)
    boll_width = (boll_upper - boll_lower) if boll_upper is not None and boll_lower is not None else None

    candle_high = float(high_series.iloc[-1]) if len(high_series) else entry_price
    candle_low = float(low_series.iloc[-1]) if len(low_series) else entry_price
    candle_open = float(open_series.iloc[-1]) if len(open_series) else entry_price
    candle_close = float(close_series.iloc[-1]) if len(close_series) else entry_price
    candle_range = max(candle_high - candle_low, 1e-9)

    volume_mean_20 = float(volume_series.tail(20).mean()) if len(volume_series) else 0.0
    volume_atual = float(volume_series.iloc[-1]) if len(volume_series) else 0.0
    volume_ratio = float(volume_atual / volume_mean_20) if volume_mean_20 > 0 else 1.0

    breakout_flags = []
    for idx in range(20, len(close_series)):
        prev_high = float(high_series.iloc[max(0, idx - 20):idx].max())
        prev_low = float(low_series.iloc[max(0, idx - 20):idx].min())
        price = float(close_series.iloc[idx])
        breakout_flags.append(price > prev_high or price < prev_low)
    candles_desde_breakout = 999.0
    if breakout_flags:
        last_break_idx = None
        for pos, flag in enumerate(breakout_flags):
            if flag:
                last_break_idx = pos
        if last_break_idx is not None:
            candles_desde_breakout = float(len(breakout_flags) - 1 - last_break_idx)

    return {
        "distancia_resistencia": _safe_div(resistencia_local - entry_price, entry_price, 0.0),
        "distancia_suporte": _safe_div(entry_price - suporte_local, entry_price, 0.0),
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "inclinacao_ema20": float(ema20 - ema20_prev),
        "distancia_preco_ema50": _safe_div(entry_price - ema50, ema50, 0.0),
        "atr_normalizado": _safe_div(atr, entry_price, 0.0),
        "largura_bollinger": _safe_div(boll_width, entry_price, 0.0) if boll_width is not None else 0.0,
        "posicao_bollinger": _safe_div(entry_price - boll_lower, boll_width, 0.5) if boll_width not in (None, 0.0) and boll_lower is not None else 0.5,
        "rsi": _to_float(last.get("RSI"), 50.0) or 50.0,
        "volume_ratio": volume_ratio,
        "candles_desde_breakout": candles_desde_breakout,
        "corpo_candle_pct": _safe_div(abs(candle_close - candle_open), candle_range, 0.0),
        "pavio_superior_pct": _safe_div(candle_high - max(candle_open, candle_close), candle_range, 0.0),
        "pavio_inferior_pct": _safe_div(min(candle_open, candle_close) - candle_low, candle_range, 0.0),
    }


def extrair_features_decisao(contexto_trade, historical_snapshot=None, context_features=None, score_maximo=10.0):
    trade = dict(contexto_trade or {})
    hist = list(historical_snapshot or [])
    ctx = dict(context_features or {})

    side_raw = str(trade.get("side") or trade.get("lado") or "").strip().lower()
    decisao_final = 1 if side_raw == "buy" else -1 if side_raw == "sell" else 0

    score_entrada = _to_float(
        trade.get("entry_score")
        or trade.get("score_entrada")
        or trade.get("consenso_score"),
        0.0,
    ) or 0.0
    score_scale = max(_to_float(score_maximo, 10.0) or 10.0, 1.0)
    score_normalizado = max(0.0, min(1.0, score_entrada / score_scale))

    lstm_signal = _normalize_lstm_signal(trade.get("decisao_lstm"))
    sentimento_signal = _normalize_sentimento(trade.get("sentimento_entrada") or trade.get("sentiment_score"))
    votes = [decisao_final, lstm_signal, sentimento_signal]
    qtd_agentes_favor = sum(1 for vote in votes if decisao_final != 0 and vote == decisao_final)
    qtd_agentes_contra = sum(1 for vote in votes if decisao_final != 0 and vote not in (0, decisao_final))
    total_agentes = max(len(votes), 1)

    rsi_value = _to_float(trade.get("rsi_entrada"), _to_float(ctx.get("rsi"), 50.0)) or 50.0
    if rsi_value < 30:
        rsi_sinal = 1
    elif rsi_value > 70:
        rsi_sinal = -1
    else:
        rsi_sinal = 0

    tendencia_signal = 0
    trend_raw = str(trade.get("tendencia") or trade.get("regime") or "").strip().lower()
    if trend_raw in {"tendencia", "alta", "bullish", "buy", "up"}:
        tendencia_signal = 1
    elif trend_raw in {"baixa", "bearish", "sell", "down"}:
        tendencia_signal = -1

    conflito_sinais = int(
        (
            rsi_sinal != 0
            and tendencia_signal != 0
            and rsi_sinal != tendencia_signal
        )
        or (
            decisao_final != 0
            and (
                (rsi_sinal != 0 and rsi_sinal != decisao_final)
                or (tendencia_signal != 0 and tendencia_signal != decisao_final)
            )
        )
    )

    tipo_estrategia = _normalize_strategy(
        trade.get("strategy") or trade.get("estrategia_utilizada") or trade.get("decision_source")
    )
    breakout_valido = int("breakout" in tipo_estrategia or "breakout" in str(trade.get("decision_source") or "").lower())

    if decisao_final >= 0:
        distancia_entrada_rompimento = _to_float(ctx.get("distancia_resistencia"), 0.0) or 0.0
    else:
        distancia_entrada_rompimento = _to_float(ctx.get("distancia_suporte"), 0.0) or 0.0
    atraso_entrada = _to_float(ctx.get("candles_desde_breakout"), 0.0) or 0.0

    hist_resultados = [int(_normalize_binary_target(item.get("resultado"))) for item in hist]
    ult10 = hist_resultados[-10:]
    ult20 = hist_resultados[-20:]
    win_rate_ultimos_10 = float(sum(ult10) / len(ult10)) if ult10 else 0.5
    win_rate_ultimos_20 = float(sum(ult20) / len(ult20)) if ult20 else 0.5
    resultado_ultimo_trade = int(ult10[-1]) if ult10 else 0
    sequencia_loss = 0
    for resultado in reversed(hist_resultados):
        if resultado == 0:
            sequencia_loss += 1
        else:
            break

    entry_price = _to_float(trade.get("entry_price") or trade.get("preco_entrada"), 0.0) or 0.0
    stop_loss = _to_float(trade.get("stop_loss"), None)
    take_profit = _to_float(trade.get("take_profit"), None)
    stop_distance_pct = abs(_safe_div(entry_price - stop_loss, entry_price, 0.0)) if stop_loss is not None else 0.0
    take_distance_pct = abs(_safe_div(take_profit - entry_price, entry_price, 0.0)) if take_profit is not None else 0.0
    rr_ratio = _safe_div(take_distance_pct, stop_distance_pct, 0.0) if stop_distance_pct > 0 else 0.0

    decision_source = str(trade.get("decision_source") or "").strip().lower()
    tipo_ordem = "limit" if "limit" in decision_source else "market"
    slippage_estimado = abs(
        _to_float(trade.get("slippage_pct"), None)
        if trade.get("slippage_pct") not in (None, "", "None")
        else _to_float(trade.get("spread_pct"), 0.0)
        or 0.0
    )

    return {
        "score_entrada_decisao": score_entrada,
        "score_normalizado": score_normalizado,
        "decisao_final": decisao_final,
        "intensidade_sinal": abs(score_entrada),
        "qtd_agentes_favor": int(qtd_agentes_favor),
        "qtd_agentes_contra": int(qtd_agentes_contra),
        "consenso_agentes": float(qtd_agentes_favor / total_agentes),
        "divergencia_agentes": float(qtd_agentes_contra / total_agentes),
        "rsi_sinal": int(rsi_sinal),
        "tendencia_sinal": int(tendencia_signal),
        "conflito_sinais": int(conflito_sinais),
        "tipo_estrategia": tipo_estrategia,
        "breakout_valido": int(breakout_valido),
        "distancia_entrada_rompimento": float(abs(distancia_entrada_rompimento)),
        "atraso_entrada": float(atraso_entrada),
        "win_rate_ultimos_10": win_rate_ultimos_10,
        "win_rate_ultimos_20": win_rate_ultimos_20,
        "resultado_ultimo_trade": int(resultado_ultimo_trade),
        "sequencia_loss": int(sequencia_loss),
        "rr_ratio": float(rr_ratio),
        "stop_distance_pct": float(stop_distance_pct),
        "take_distance_pct": float(take_distance_pct),
        "tipo_ordem": tipo_ordem,
        "slippage_estimado": float(slippage_estimado),
    }


def calcular_brier_score(y_true, y_prob):
    y_true_arr = np.asarray(list(y_true or []), dtype=float)
    y_prob_arr = np.asarray(list(y_prob or []), dtype=float)
    if len(y_true_arr) == 0 or len(y_true_arr) != len(y_prob_arr):
        return 0.0
    return float(np.mean((y_prob_arr - y_true_arr) ** 2))


def calcular_calibracao_por_bucket(trades, n_bins=10):
    rows = list(trades or [])
    if not rows:
        return []

    bin_edges = np.linspace(0.50, 1.00, n_bins + 1)
    buckets = []
    for idx in range(n_bins):
        low = float(bin_edges[idx])
        high = float(bin_edges[idx + 1])
        selected = []
        for trade in rows:
            prob = _to_float(trade.get("trade_quality_prob") or trade.get("probabilidade"), None)
            resultado = trade.get("resultado")
            if prob is None or resultado in (None, ""):
                continue
            inside = (low <= prob <= high) if idx == n_bins - 1 else (low <= prob < high)
            if inside:
                selected.append(
                    {
                        "prob": float(prob),
                        "resultado": _normalize_binary_target(resultado),
                    }
                )

        if len(selected) < MIN_BUCKET_TRADES:
            continue

        prob_media = float(np.mean([item["prob"] for item in selected]))
        win_rate_real = float(np.mean([item["resultado"] for item in selected]))
        buckets.append(
            {
                "faixa": f"{low:.2f}-{high:.2f}",
                "prob_media": round(prob_media, 4),
                "win_rate_real": round(win_rate_real, 4),
                "erro_calibracao": round(abs(prob_media - win_rate_real), 4),
                "qtd_trades": len(selected),
            }
        )

    return buckets


def gerar_relatorio_calibracao(trades):
    rows = list(trades or [])
    y_true = []
    y_prob = []
    for trade in rows:
        prob = _to_float(trade.get("trade_quality_prob") or trade.get("probabilidade"), None)
        resultado = trade.get("resultado")
        if prob is None or resultado in (None, ""):
            continue
        y_true.append(_normalize_binary_target(resultado))
        y_prob.append(float(prob))

    buckets = calcular_calibracao_por_bucket(rows)
    brier = calcular_brier_score(y_true, y_prob)
    erro_medio = float(np.mean([b["erro_calibracao"] for b in buckets])) if buckets else 0.0
    melhor_bucket = min(buckets, key=lambda x: x["erro_calibracao"]) if buckets else None
    pior_bucket = max(buckets, key=lambda x: x["erro_calibracao"]) if buckets else None

    relatorio = {
        "brier_score": round(float(brier), 6),
        "erro_medio_calibracao": round(float(erro_medio), 6),
        "melhor_bucket": melhor_bucket,
        "pior_bucket": pior_bucket,
        "total_trades": len(y_true),
        "buckets": buckets,
        "gerado_em": datetime.utcnow().isoformat(),
    }
    _safe_json_dump(_VALIDATION_REPORT_PATH, relatorio)

    history = _safe_json_load(_CALIBRATION_HISTORY_PATH, [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "timestamp": relatorio["gerado_em"],
            "brier_score": relatorio["brier_score"],
            "erro_medio_calibracao": relatorio["erro_medio_calibracao"],
            "total_trades": relatorio["total_trades"],
        }
    )
    _safe_json_dump(_CALIBRATION_HISTORY_PATH, history[-200:])
    return relatorio


def avaliar_monotonicidade(trades):
    buckets = calcular_calibracao_por_bucket(trades)
    if len(buckets) < 2:
        return {
            "monotonicidade": False,
            "correlacao": 0.0,
            "confiavel": False,
        }

    prob_medias = np.asarray([b["prob_media"] for b in buckets], dtype=float)
    win_rates = np.asarray([b["win_rate_real"] for b in buckets], dtype=float)
    try:
        correlacao = float(np.corrcoef(prob_medias, win_rates)[0][1])
        if np.isnan(correlacao):
            correlacao = 0.0
    except Exception:
        correlacao = 0.0

    monotonicidade = bool(all(win_rates[i] <= win_rates[i + 1] for i in range(len(win_rates) - 1)))
    return {
        "monotonicidade": monotonicidade,
        "correlacao": round(correlacao, 6),
        "confiavel": bool(monotonicidade and correlacao >= MIN_MONOTONIC_CORRELATION),
    }


def analisar_impacto_filtro(trades):
    rows = list(trades or [])
    total = len(rows)
    if not total:
        return {
            "trades_removidos_pct": 0.0,
            "lucro_perdido": 0.0,
            "lucro_evitar_prejuizo": 0.0,
            "eficiencia_filtro": 0.0,
        }

    removed = [t for t in rows if bool(t.get("teria_bloqueado"))]
    removed_pct = len(removed) / total if total else 0.0
    lucro_perdido = sum((_to_float(t.get("pnl_abs"), 0.0) or 0.0) for t in removed if (_to_float(t.get("pnl_abs"), 0.0) or 0.0) > 0)
    prejuizos_evitar = abs(sum((_to_float(t.get("pnl_abs"), 0.0) or 0.0) for t in removed if (_to_float(t.get("pnl_abs"), 0.0) or 0.0) < 0))
    eficiencia = (prejuizos_evitar / (lucro_perdido + prejuizos_evitar)) if (lucro_perdido + prejuizos_evitar) > 0 else 0.0
    return {
        "trades_removidos_pct": round(removed_pct, 6),
        "lucro_perdido": round(float(lucro_perdido), 6),
        "lucro_evitar_prejuizo": round(float(prejuizos_evitar), 6),
        "eficiencia_filtro": round(float(eficiencia), 6),
    }


def detectar_drift(dataset_atual, dataset_treino):
    atual = dataset_atual if isinstance(dataset_atual, pd.DataFrame) else pd.DataFrame(dataset_atual or [])
    treino = dataset_treino if isinstance(dataset_treino, pd.DataFrame) else pd.DataFrame(dataset_treino or [])
    if atual.empty or treino.empty:
        return {
            "drift_detectado": False,
            "nivel_drift": "baixo",
            "feature_shift_medio": 0.0,
            "win_rate_shift": 0.0,
        }

    shifts = []
    for col in _FEATURE_COLUMNS:
        if col in atual.columns and col in treino.columns:
            try:
                a = pd.to_numeric(atual[col], errors="coerce").dropna()
                b = pd.to_numeric(treino[col], errors="coerce").dropna()
                if len(a) and len(b):
                    mean_b = max(abs(float(b.mean())), 1e-9)
                    shifts.append(abs(float(a.mean()) - float(b.mean())) / mean_b)
            except Exception:
                continue

    feature_shift = float(np.mean(shifts)) if shifts else 0.0
    win_rate_atual = float(pd.to_numeric(atual.get("resultado"), errors="coerce").fillna(0).mean())
    win_rate_treino = float(pd.to_numeric(treino.get("resultado"), errors="coerce").fillna(0).mean())
    win_rate_shift = abs(win_rate_atual - win_rate_treino)
    score_drift = max(feature_shift, win_rate_shift)

    if score_drift >= 0.30:
        nivel = "alto"
    elif score_drift >= 0.15:
        nivel = "medio"
    else:
        nivel = "baixo"

    return {
        "drift_detectado": nivel != "baixo",
        "nivel_drift": nivel,
        "feature_shift_medio": round(feature_shift, 6),
        "win_rate_shift": round(win_rate_shift, 6),
    }


def gerar_relatorio_validacao(trades=None):
    dataset = carregar_dataset()
    rows = list(trades or [])
    calibracao = gerar_relatorio_calibracao(rows)
    monotonicidade = avaliar_monotonicidade(rows)
    impacto = analisar_impacto_filtro(rows)
    training_profile = (carregar_modelo() or {}).get("training_profile") or {}
    dataset_treino = dataset.copy() if training_profile.get("samples") else dataset
    drift = detectar_drift(dataset.tail(200), dataset_treino)

    enabled = True
    motivos = []
    if float(calibracao.get("brier_score") or 0.0) > MAX_BRIER_SCORE:
        enabled = False
        motivos.append("brier_score_alto")
    if not bool(monotonicidade.get("monotonicidade")):
        enabled = False
        motivos.append("monotonicidade_false")
    if str(drift.get("nivel_drift")) == "alto":
        enabled = False
        motivos.append("drift_alto")

    relatorio = {
        "enabled": enabled,
        "motivos_desativacao": motivos,
        "calibracao": calibracao,
        "monotonicidade": monotonicidade,
        "impacto_filtro": impacto,
        "drift": drift,
        "dataset_samples": int(len(dataset)),
        "gerado_em": datetime.utcnow().isoformat(),
    }
    _safe_json_dump(_VALIDATION_REPORT_PATH, relatorio)
    return relatorio


def verificar_retreinamento() -> dict:
    dataset = carregar_dataset()
    total = len(dataset)
    if total >= MIN_TRAIN_SAMPLES and total % RETRAIN_EVERY_N_TRADES == 0:
        treino = treinar_modelo(dataset=dataset, force=True)
        try:
            gerar_relatorio_validacao()
        except Exception as e:
            logger.warning("Falha ao atualizar relatórios de validação: %s", e)
        return treino
    return {
        "trained": False,
        "motivo": "retrain_nao_necessario",
        "samples": total,
    }


def registrar_trade_quality_fechado(dados_trade: dict) -> bool:
    payload = dict(dados_trade or {})
    pnl_abs = _to_float(payload.get("pnl_abs"), None)
    lucro = _to_float(payload.get("lucro"), None)
    prejuizo = _to_float(payload.get("prejuizo"), None)
    if pnl_abs is None:
        if lucro not in (None, 0.0):
            pnl_abs = float(lucro)
        elif prejuizo not in (None, 0.0):
            pnl_abs = -abs(float(prejuizo))
        else:
            pnl_abs = 0.0
    if lucro is None:
        lucro = float(pnl_abs) if float(pnl_abs) > 0 else 0.0
    if prejuizo is None:
        prejuizo = abs(float(pnl_abs)) if float(pnl_abs) < 0 else 0.0

    row = {
        "timestamp": _safe_timestamp(payload.get("timestamp") or payload.get("timestamp_saida")),
        "rsi": _to_float(payload.get("rsi"), 50.0),
        "atr": _to_float(payload.get("atr"), 0.0),
        "volume_ratio": _to_float(payload.get("volume_ratio"), 1.0),
        "lstm_signal": _normalize_lstm_signal(payload.get("lstm_signal")),
        "sentimento": _normalize_sentimento(payload.get("sentimento")),
        "estrategia": _normalize_strategy(payload.get("estrategia")),
        "tendencia": _normalize_trend(payload.get("tendencia") or payload.get("regime")),
        "volatilidade": _to_float(payload.get("volatilidade"), 0.0),
        "score_entrada": _to_float(payload.get("score_entrada"), 0.0),
        "distancia_resistencia": _to_float(payload.get("distancia_resistencia"), 0.0),
        "distancia_suporte": _to_float(payload.get("distancia_suporte"), 0.0),
        "inclinacao_ema50": _to_float(payload.get("inclinacao_ema50"), 0.0),
        "inclinacao_ema200": _to_float(payload.get("inclinacao_ema200"), 0.0),
        "atr_normalizado": _to_float(payload.get("atr_normalizado"), 0.0),
        "posicao_bollinger": _to_float(payload.get("posicao_bollinger"), 0.5),
        "candles_desde_breakout": _to_float(payload.get("candles_desde_breakout"), 0.0),
        "confluencia_sinais": int(_to_float(payload.get("confluencia_sinais"), 0.0) or 0.0),
        "resultado": _normalize_binary_target(payload.get("resultado")),
        "lucro": lucro,
        "prejuizo": prejuizo,
        "pnl_abs": pnl_abs,
        "drawdown": _to_float(payload.get("drawdown"), 0.0) or 0.0,
        "probabilidade": _to_float(payload.get("probabilidade"), None),
        "flag_filtrado": int(bool(payload.get("flag_filtrado"))) if payload.get("flag_filtrado") is not None else None,
        "teria_bloqueado": int(bool(payload.get("teria_bloqueado"))) if payload.get("teria_bloqueado") is not None else None,
        "modelo_status": str(payload.get("modelo_status") or ""),
    }

    try:
        with _LOCK:
            _ensure_storage()
            with _quality_connect() as conn:
                cursor = conn.cursor()
                storage_columns = list(_STORAGE_COLUMNS)
                placeholders = ", ".join("%s" if _using_postgres() else "?" for _ in storage_columns)
                cursor.execute(
                    _quality_sql(f"""
                    INSERT INTO trade_quality_samples (
                        timestamp, rsi, atr, volume_ratio, lstm_signal, sentimento,
                        estrategia, tendencia, volatilidade, score_entrada,
                        distancia_resistencia, distancia_suporte, inclinacao_ema50,
                        inclinacao_ema200, atr_normalizado, posicao_bollinger,
                        candles_desde_breakout, confluencia_sinais, resultado,
                        lucro, prejuizo, pnl_abs, drawdown, probabilidade,
                        flag_filtrado, teria_bloqueado, modelo_status
                    ) VALUES ({placeholders})
                    """),
                    (
                        row["timestamp"],
                        row["rsi"],
                        row["atr"],
                        row["volume_ratio"],
                        row["lstm_signal"],
                        row["sentimento"],
                        row["estrategia"],
                        row["tendencia"],
                        row["volatilidade"],
                        row["score_entrada"],
                        row["distancia_resistencia"],
                        row["distancia_suporte"],
                        row["inclinacao_ema50"],
                        row["inclinacao_ema200"],
                        row["atr_normalizado"],
                        row["posicao_bollinger"],
                        row["candles_desde_breakout"],
                        row["confluencia_sinais"],
                        row["resultado"],
                        row["lucro"],
                        row["prejuizo"],
                        row["pnl_abs"],
                        row["drawdown"],
                        row["probabilidade"],
                        row["flag_filtrado"],
                        row["teria_bloqueado"],
                        row["modelo_status"],
                    ),
                )
                conn.commit()
            _append_csv(row)
            verificar_retreinamento()
        return True
    except Exception as e:
        logger.warning("Falha ao registrar dataset do Trade Quality: %s", e)
        try:
            _append_csv(row)
            return True
        except Exception as csv_e:
            logger.warning("Falha também no fallback CSV do Trade Quality: %s", csv_e)
            return False


def prever_probabilidade_trade(features):
    dataset = carregar_dataset()
    if len(dataset) < MIN_TRAIN_SAMPLES:
        return 0.5, "fallback_poucos_dados"

    bundle = carregar_modelo()
    if bundle is None:
        return 0.5, "fallback_sem_modelo"

    model = bundle.get("model")
    feature_columns = list(bundle.get("feature_columns") or [])
    if model is None or not feature_columns:
        return 0.5, "fallback_sem_modelo"

    feat = dict(features or {})
    base = pd.DataFrame(
        [
            {
                "rsi": _to_float(feat.get("rsi"), 50.0),
                "atr": _to_float(feat.get("atr"), 0.0),
                "volume_ratio": _to_float(feat.get("volume_ratio"), 1.0),
                "lstm_signal": _normalize_lstm_signal(feat.get("lstm_signal")),
                "sentimento": _normalize_sentimento(feat.get("sentimento")),
                "estrategia": _normalize_strategy(feat.get("estrategia")),
                "tendencia": _normalize_trend(feat.get("tendencia")),
                "volatilidade": _to_float(feat.get("volatilidade"), 0.0),
                "score_entrada": _to_float(feat.get("score_entrada"), 0.0),
                "distancia_resistencia": _to_float(feat.get("distancia_resistencia"), 0.0),
                "distancia_suporte": _to_float(feat.get("distancia_suporte"), 0.0),
                "inclinacao_ema50": _to_float(feat.get("inclinacao_ema50"), 0.0),
                "inclinacao_ema200": _to_float(feat.get("inclinacao_ema200"), 0.0),
                "atr_normalizado": _to_float(feat.get("atr_normalizado"), 0.0),
                "posicao_bollinger": _to_float(feat.get("posicao_bollinger"), 0.5),
                "candles_desde_breakout": _to_float(feat.get("candles_desde_breakout"), 0.0),
                "confluencia_sinais": int(_to_float(feat.get("confluencia_sinais"), 0.0) or 0.0),
            }
        ]
    )
    X = pd.get_dummies(base, columns=["estrategia", "tendencia"], dummy_na=False)
    X = X.reindex(columns=feature_columns, fill_value=0)

    try:
        prob = float(model.predict_proba(X)[0][1])
        return max(0.0, min(1.0, prob)), "modelo_ativo"
    except Exception as e:
        logger.warning("Falha ao prever Trade Quality Score: %s", e)
        return 0.5, "fallback_erro_predicao"


def calcular_threshold_dinamico(metricas):
    win_rate = _to_float((metricas or {}).get("win_rate"), 0.5) or 0.5
    if win_rate < 0.55:
        return 0.50
    if win_rate < 0.60:
        return 0.53
    if win_rate < 0.65:
        return 0.55
    return 0.58


def calcular_score_hibrido(score_heuristico, probabilidade):
    score_norm = min((_to_float(score_heuristico, 0.0) or 0.0) / 10.0, 1.0)
    prob = max(0.0, min(1.0, _to_float(probabilidade, 0.5) or 0.5))
    return (score_norm * 0.6) + (prob * 0.4)


def avaliar_decisao_trade_quality(
    probabilidade: float,
    score_heuristico: float,
    metricas: dict | None = None,
    features: dict | None = None,
    modelo_status: str = "fallback_sem_modelo",
    sample_count: int = 0,
) -> dict:
    prob = max(0.0, min(1.0, float(probabilidade or 0.0)))
    threshold = float(calcular_threshold_dinamico(metricas or {}))
    score_hibrido = float(calcular_score_hibrido(score_heuristico, prob))
    validation_report = _safe_json_load(_VALIDATION_REPORT_PATH, {})
    auto_disabled = bool(validation_report) and not bool(validation_report.get("enabled", True))
    mode = str(os.getenv("TRADE_QUALITY_MODE", "active")).strip().lower()

    if auto_disabled:
        decisao = "entrada"
        lot_factor = 1.0
        motivo = "trade_quality_auto_desativado"
        teria_bloqueado = bool(prob < threshold or score_hibrido < 0.55)
    elif modelo_status != "modelo_ativo" or int(sample_count or 0) < MIN_CONFIDENCE_SAMPLES:
        decisao = "entrada"
        lot_factor = 1.0
        motivo = modelo_status if modelo_status != "modelo_ativo" else "modelo_baixa_confianca"
        teria_bloqueado = False
    else:
        teria_bloqueado = bool(prob < threshold or score_hibrido < 0.55)
        if mode == "shadow":
            decisao = "entrada"
            lot_factor = 1.0
            motivo = "shadow_mode"
        elif teria_bloqueado:
            decisao = "bloqueado"
            lot_factor = 0.0
            motivo = "trade_quality_bloqueado"
        elif score_hibrido < 0.65:
            decisao = "entrada_reduzida"
            lot_factor = 0.8 if prob >= 0.60 else 0.5
            motivo = "trade_quality_entrada_reduzida"
        else:
            decisao = "entrada"
            lot_factor = 1.0
            motivo = "trade_quality_entrada_normal"

    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "probabilidade": round(prob, 4),
        "threshold": round(threshold, 4),
        "score_base": round(float(_to_float(score_heuristico, 0.0) or 0.0), 4),
        "score_hibrido": round(score_hibrido, 4),
        "decisao": decisao,
        "motivo": motivo,
        "modelo_status": modelo_status,
        "sample_count": int(sample_count or 0),
        "lot_factor": round(float(lot_factor), 4),
        "teria_bloqueado": teria_bloqueado,
        "resultado_real": None,
        "mode": mode,
        "features": dict(features or {}),
    }
    _write_prediction_log(payload)
    return payload


def _calc_drawdown_from_trades(trades: list[dict]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        equity += _to_float(trade.get("pnl_abs"), 0.0) or 0.0
        peak = max(peak, equity)
        drawdown = peak - equity
        max_dd = max(max_dd, drawdown)
    return float(max_dd)


def calcular_metricas_backtest_trade_quality(trade_log: list[dict], prob_threshold: float | None = None) -> dict:
    trades = list(trade_log or [])
    if not trades:
        empty_metrics = {
            "win_rate": 0.0,
            "lucro_total": 0.0,
            "lucro_medio": 0.0,
            "drawdown": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
        }
        return {
            "baseline": dict(empty_metrics),
            "filtrado": dict(empty_metrics),
            "delta": {
                "win_rate": 0.0,
                "lucro_total": 0.0,
                "drawdown": 0.0,
                "profit_factor": 0.0,
            },
            "threshold_utilizado": prob_threshold if prob_threshold is not None else 0.0,
        }

    avg_win_rate = sum(max(0.0, min(1.0, _to_float(t.get("trade_quality_prob"), 0.5) or 0.5)) for t in trades) / len(trades)
    threshold = float(prob_threshold if prob_threshold is not None else calcular_threshold_dinamico({"win_rate": avg_win_rate}))
    filtrados = [trade for trade in trades if (_to_float(trade.get("trade_quality_prob"), None) or -1.0) >= threshold]

    def _profit_factor(rows):
        gains = sum((_to_float(t.get("pnl_abs"), 0.0) or 0.0) for t in rows if (_to_float(t.get("pnl_abs"), 0.0) or 0.0) > 0)
        losses = abs(sum((_to_float(t.get("pnl_abs"), 0.0) or 0.0) for t in rows if (_to_float(t.get("pnl_abs"), 0.0) or 0.0) < 0))
        if losses == 0:
            return float(gains) if gains > 0 else 0.0
        return float(gains / losses)

    def _metrics(rows):
        total = len(rows)
        pnl_total = sum((_to_float(t.get("pnl_abs"), 0.0) or 0.0) for t in rows)
        wins = sum(1 for t in rows if (_to_float(t.get("pnl_abs"), 0.0) or 0.0) > 0)
        return {
            "win_rate": (wins / total) if total else 0.0,
            "lucro_total": float(pnl_total),
            "lucro_medio": float(pnl_total / total) if total else 0.0,
            "drawdown": _calc_drawdown_from_trades(rows),
            "profit_factor": _profit_factor(rows),
            "total_trades": total,
        }

    baseline = _metrics(trades)
    filtrado = _metrics(filtrados)
    return {
        "baseline": baseline,
        "filtrado": filtrado,
        "delta": {
            "win_rate": round(float(filtrado["win_rate"] - baseline["win_rate"]), 6),
            "lucro_total": round(float(filtrado["lucro_total"] - baseline["lucro_total"]), 6),
            "drawdown": round(float(filtrado["drawdown"] - baseline["drawdown"]), 6),
            "profit_factor": round(float(filtrado["profit_factor"] - baseline["profit_factor"]), 6),
        },
        "threshold_utilizado": round(threshold, 4),
    }


def _normalizar_trade_relatorio(trade: dict) -> dict:
    row = dict(trade or {})
    prob = _to_float(row.get("probabilidade"), None)
    if prob is None:
        prob = _to_float(row.get("trade_quality_prob"), 0.5)

    resultado = row.get("resultado")
    if resultado in (None, ""):
        pnl_guess = _to_float(row.get("lucro"), None)
        if pnl_guess is None:
            pnl_guess = _to_float(row.get("pnl_abs"), 0.0)
        resultado = 1 if (pnl_guess or 0.0) > 0 else 0

    lucro = _to_float(row.get("lucro"), None)
    if lucro is None:
        pnl = _to_float(row.get("pnl_abs"), 0.0) or 0.0
        lucro = pnl if pnl > 0 else 0.0

    prejuizo = _to_float(row.get("prejuizo"), None)
    if prejuizo is None:
        pnl = _to_float(row.get("pnl_abs"), 0.0) or 0.0
        prejuizo = abs(pnl) if pnl < 0 else 0.0

    drawdown = _to_float(row.get("drawdown"), None)
    if drawdown is None:
        drawdown = 0.0

    flag_filtrado = row.get("flag_filtrado")
    if pd.isna(flag_filtrado):
        flag_filtrado = None
    if flag_filtrado is None:
        flag_filtrado = not bool(row.get("teria_bloqueado"))
    teria_bloqueado = row.get("teria_bloqueado")
    if pd.isna(teria_bloqueado):
        teria_bloqueado = None

    return {
        "resultado": _normalize_binary_target(resultado),
        "probabilidade": max(0.0, min(1.0, float(prob or 0.5))),
        "lucro": float(lucro or 0.0),
        "prejuizo": float(prejuizo or 0.0),
        "drawdown": float(drawdown or 0.0),
        "flag_filtrado": bool(flag_filtrado),
        "teria_bloqueado": bool(teria_bloqueado) if teria_bloqueado is not None else (not bool(flag_filtrado)),
    }


def _metricas_financeiras(rows: list[dict]) -> dict:
    total = len(rows)
    wins = sum(1 for row in rows if int(row.get("resultado", 0)) == 1)
    losses = max(total - wins, 0)
    lucro_total = sum(float(row.get("lucro") or 0.0) for row in rows)
    prejuizo_total = sum(float(row.get("prejuizo") or 0.0) for row in rows)
    lucro_medio = (lucro_total / wins) if wins else 0.0
    perda_media = (prejuizo_total / losses) if losses else 0.0
    win_rate = (wins / total) if total else 0.0
    loss_rate = (losses / total) if total else 0.0
    ev = (win_rate * lucro_medio) - (loss_rate * perda_media)
    profit_factor = (lucro_total / abs(prejuizo_total)) if prejuizo_total > 0 else (float(lucro_total) if lucro_total > 0 else 0.0)
    drawdown = max((float(row.get("drawdown") or 0.0) for row in rows), default=0.0)
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(float(win_rate), 6),
        "loss_rate": round(float(loss_rate), 6),
        "lucro_total": round(float(lucro_total), 6),
        "prejuizo_total": round(float(prejuizo_total), 6),
        "lucro_medio": round(float(lucro_medio), 6),
        "perda_media": round(float(perda_media), 6),
        "EV": round(float(ev), 6),
        "profit_factor": round(float(profit_factor), 6),
        "drawdown": round(float(drawdown), 6),
    }


def avaliar_modelo(relatorio):
    motivos = []
    status = "aprovado"
    confiabilidade = 100.0

    brier = float(((relatorio or {}).get("estatisticas") or {}).get("brier_score") or 0.0)
    auc = float(((relatorio or {}).get("estatisticas") or {}).get("auc") or 0.0)
    baseline = (relatorio or {}).get("comparacao", {}).get("baseline", {})
    filtrado = (relatorio or {}).get("comparacao", {}).get("filtrado", {})

    if brier > 0.25:
        motivos.append("brier_score_alto")
        confiabilidade -= 30
    if auc < 0.6:
        motivos.append("sem_poder_preditivo")
        confiabilidade -= 30
    if float(filtrado.get("profit_factor", 0.0)) < float(baseline.get("profit_factor", 0.0)):
        motivos.append("profit_factor_piora")
        confiabilidade -= 20
    if float(filtrado.get("EV", 0.0)) < float(baseline.get("EV", 0.0)):
        motivos.append("ev_nao_gera_valor")
        confiabilidade -= 20

    if {"brier_score_alto", "sem_poder_preditivo"} & set(motivos):
        status = "reprovado"
    elif motivos:
        status = "instavel"

    if not motivos:
        recomendacao = "Modelo melhora ou preserva o sistema com sinais estatísticos aceitáveis."
    elif status == "reprovado":
        recomendacao = "Modelo não deve ser usado para decisão operacional até nova calibração."
    else:
        recomendacao = "Modelo exige monitoramento e calibração adicional antes de uso mais forte."

    return {
        "status_modelo": status,
        "confiabilidade": max(0.0, min(100.0, round(confiabilidade, 2))),
        "recomendacao": recomendacao,
        "motivos": motivos,
    }


def gerar_relatorio_trade_quality(trades):
    rows = [_normalizar_trade_relatorio(trade) for trade in list(trades or [])]
    y_true = [row["resultado"] for row in rows]
    y_prob = [row["probabilidade"] for row in rows]

    brier_score = calcular_brier_score(y_true, y_prob)
    try:
        auc = float(roc_auc_score(y_true, y_prob)) if len(set(y_true)) > 1 else 0.5
    except Exception:
        auc = 0.5

    buckets = calcular_calibracao_por_bucket(
        [
            {"resultado": row["resultado"], "probabilidade": row["probabilidade"]}
            for row in rows
        ]
    )
    monotonicidade = avaliar_monotonicidade(
        [
            {"resultado": row["resultado"], "probabilidade": row["probabilidade"]}
            for row in rows
        ]
    )

    baseline_rows = rows
    filtrado_rows = [row for row in rows if row["flag_filtrado"]]
    baseline = _metricas_financeiras(baseline_rows)
    filtrado = _metricas_financeiras(filtrado_rows)
    comparacao = {
        "baseline": baseline,
        "filtrado": filtrado,
        "delta": {
            "win_rate": round(float(filtrado["win_rate"] - baseline["win_rate"]), 6),
            "lucro_total": round(float(filtrado["lucro_total"] - baseline["lucro_total"]), 6),
            "drawdown": round(float(filtrado["drawdown"] - baseline["drawdown"]), 6),
            "profit_factor": round(float(filtrado["profit_factor"] - baseline["profit_factor"]), 6),
            "EV": round(float(filtrado["EV"] - baseline["EV"]), 6),
        },
    }

    impacto = {
        "trades_removidos_pct": round((1.0 - (len(filtrado_rows) / len(rows))) if rows else 0.0, 6),
        "lucro_removido": round(sum(float(row["lucro"]) for row in rows if not row["flag_filtrado"]), 6),
        "prejuizo_evitar": round(sum(float(row["prejuizo"]) for row in rows if not row["flag_filtrado"]), 6),
        "eficiencia_filtro": round(
            sum(float(row["prejuizo"]) for row in rows if not row["flag_filtrado"])
            - sum(float(row["lucro"]) for row in rows if not row["flag_filtrado"]),
            6,
        ),
    }

    relatorio = {
        "estatisticas": {
            "brier_score": round(float(brier_score), 6),
            "auc": round(float(auc), 6),
            "calibracao_por_bucket": buckets,
            "monotonicidade": monotonicidade,
        },
        "comparacao": comparacao,
        "impacto_filtro": impacto,
        "total_trades": len(rows),
        "gerado_em": datetime.utcnow().isoformat(),
    }
    relatorio["avaliacao_modelo"] = avaliar_modelo(relatorio)

    _safe_json_dump(_FINAL_REPORT_PATH, relatorio)

    print("=== TRADE QUALITY REPORT ===")
    print(f"Brier Score: {relatorio['estatisticas']['brier_score']:.6f}")
    print(f"AUC: {relatorio['estatisticas']['auc']:.6f}")
    print(f"Win Rate Δ: {comparacao['delta']['win_rate']:.6f}")
    print(f"Profit Factor Δ: {comparacao['delta']['profit_factor']:.6f}")
    print(f"EV Δ: {comparacao['delta']['EV']:.6f}")
    print(f"Drawdown Δ: {comparacao['delta']['drawdown']:.6f}")
    print(f"Status: {relatorio['avaliacao_modelo']['status_modelo']}")

    return relatorio


def _parse_timestamp_any(value):
    if value in (None, "", "None"):
        return None
    try:
        return pd.to_datetime(value, utc=False)
    except Exception:
        return None


def _trade_quality_signature(payload: dict) -> tuple:
    return (
        str(payload.get("timestamp") or ""),
        str(payload.get("estrategia") or ""),
        str(payload.get("tendencia") or ""),
        round(float(_to_float(payload.get("score_entrada"), 0.0) or 0.0), 6),
        int(_normalize_binary_target(payload.get("resultado"))),
    )


def _load_existing_trade_quality_signatures() -> set[tuple]:
    dataset = carregar_dataset()
    signatures = set()
    if dataset.empty:
        return signatures

    for _, row in dataset.iterrows():
        signatures.add(
            _trade_quality_signature(
                {
                    "timestamp": row.get("timestamp"),
                    "estrategia": row.get("estrategia"),
                    "tendencia": row.get("tendencia"),
                    "score_entrada": row.get("score_entrada"),
                    "resultado": row.get("resultado"),
                }
            )
        )
    return signatures


def _load_trade_rows_from_main_db(limit: int | None = None) -> list[dict]:
    if not _using_postgres() and not (Path(__file__).resolve().parents[1] / "trades.db").exists():
        return []

    query = """
        SELECT *
        FROM trades
        WHERE (status = 'fechado' OR close_ts IS NOT NULL OR exit_price IS NOT NULL)
          AND symbol IS NOT NULL
          AND entry_price IS NOT NULL
        ORDER BY id ASC
    """
    if isinstance(limit, int) and limit > 0:
        query += f" LIMIT {int(limit)}"

    conn = _main_trade_connect()
    try:
        rows = [dict(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()
    return rows


def _build_main_db_trade_index() -> dict[str, dict]:
    index = {}
    for row in _load_trade_rows_from_main_db():
        ts = str(row.get("open_ts") or row.get("timestamp") or "").strip()
        if ts and ts not in index:
            index[ts] = row
    return index


def _load_clean_trade_rows(view_name: str = "trades_clean_recent_350") -> list[dict]:
    if not _using_postgres() and not (Path(__file__).resolve().parents[1] / "trades.db").exists():
        return []

    conn = _main_trade_connect()
    try:
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {view_name}").fetchall()]
    finally:
        conn.close()
    return rows


def _load_market_context_from_csv(symbol: str, entry_ts) -> pd.DataFrame | None:
    pattern = f"market_data_{str(symbol).upper()}_5m.csv*"
    candidates = sorted(glob.glob(pattern), key=lambda p: os.path.getsize(p), reverse=True)
    for path in candidates:
        try:
            df = pd.read_csv(path)
            if "time" not in df.columns:
                continue
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
            df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
            if df.empty:
                continue
            if entry_ts is not None:
                df_slice = df[df["time"] <= entry_ts].tail(220).copy()
            else:
                df_slice = df.tail(220).copy()
            if len(df_slice) < 30:
                continue
            return calculate_indicators(df_slice.copy())
        except Exception:
            continue
    return None


def _load_market_context_from_csv_recursive(symbol: str, entry_ts) -> pd.DataFrame | None:
    patterns = [
        f"**/market_data_{str(symbol).upper()}_5m.csv",
        f"market_data_{str(symbol).upper()}_5m.csv",
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern, recursive=True))
    ranked = sorted(set(candidates), key=lambda p: (os.path.getmtime(p), os.path.getsize(p)), reverse=True)

    for path in ranked:
        try:
            df = pd.read_csv(path)
            if "time" not in df.columns:
                continue
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
            df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
            if df.empty:
                continue
            if entry_ts is not None:
                df_slice = df[df["time"] <= entry_ts].tail(220).copy()
            else:
                df_slice = df.tail(220).copy()
            if len(df_slice) < 30:
                continue
            return df_slice
        except Exception:
            continue
    return None


def _load_full_market_context_from_csv_recursive(symbol: str) -> pd.DataFrame | None:
    patterns = [
        f"**/market_data_{str(symbol).upper()}_5m.csv",
        f"market_data_{str(symbol).upper()}_5m.csv",
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern, recursive=True))
    ranked = sorted(set(candidates), key=lambda p: (os.path.getmtime(p), os.path.getsize(p)), reverse=True)

    for path in ranked:
        try:
            df = pd.read_csv(path)
            if "time" not in df.columns:
                continue
            df["time"] = pd.to_datetime(df["time"], errors="coerce")
            df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
            if len(df) < 30:
                continue
            return calculate_indicators(df.copy())
        except Exception:
            continue
    return None


def _load_market_context_from_api(symbol: str) -> pd.DataFrame | None:
    try:
        session = initialize_session()
        df = get_market_data_bybit(str(symbol).upper(), session, interval="5", limit=220, save_csv=False)
        if df is None or df.empty:
            return None
        return calculate_indicators(df.copy())
    except Exception as e:
        logger.warning("Falha ao obter candles da API para importacao de %s: %s", symbol, e)
        return None


def _build_trade_quality_payload_from_db_row(row: dict) -> dict | None:
    entry_ts = _parse_timestamp_any(row.get("open_ts") or row.get("timestamp") or row.get("timestamp_decisao_ia"))
    symbol = str(row.get("symbol") or "").upper().strip()
    if not symbol:
        return None

    df_ctx = _load_market_context_from_csv(symbol, entry_ts)
    if df_ctx is None or df_ctx.empty:
        df_ctx = _load_market_context_from_api(symbol)

    if df_ctx is not None and not df_ctx.empty:
        contexto = {
            "rsi": row.get("rsi_entrada"),
            "atr": row.get("atr_entrada"),
            "sentimento": row.get("sentimento_entrada") or row.get("sentiment_score"),
            "estrategia": row.get("strategy") or row.get("estrategia_utilizada"),
            "tendencia": row.get("tendencia") or row.get("regime"),
            "regime": row.get("regime"),
            "score_entrada": row.get("entry_score"),
            "decisao_lstm": row.get("decisao_lstm"),
        }
        features = extrair_features_trade(df_ctx.tail(220).copy(), contexto)
    else:
        entry_price = _to_float(row.get("entry_price"), 0.0) or 0.0
        atr_val = _to_float(row.get("atr_entrada"), 0.0) or 0.0
        features = {
            "rsi": _to_float(row.get("rsi_entrada"), 50.0) or 50.0,
            "atr": atr_val,
            "volume_ratio": 1.0,
            "lstm_signal": _normalize_lstm_signal(row.get("decisao_lstm")),
            "sentimento": _normalize_sentimento(row.get("sentimento_entrada") or row.get("sentiment_score")),
            "estrategia": _normalize_strategy(row.get("strategy") or row.get("estrategia_utilizada")),
            "tendencia": _normalize_trend(row.get("tendencia") or row.get("regime")),
            "volatilidade": float(atr_val / entry_price) if entry_price > 0 else 0.0,
            "score_entrada": _to_float(row.get("entry_score"), 0.0) or 0.0,
            "distancia_resistencia": 0.0,
            "distancia_suporte": 0.0,
            "inclinacao_ema50": 0.0,
            "inclinacao_ema200": 0.0,
            "atr_normalizado": float(atr_val / entry_price) if entry_price > 0 else 0.0,
            "posicao_bollinger": 0.5,
            "candles_desde_breakout": 0.0,
            "confluencia_sinais": 0,
        }

    resultado = 1 if (_to_float(row.get("profit_loss"), 0.0) or 0.0) > 0 else 0
    pnl_abs = _to_float(row.get("profit_loss"), 0.0) or 0.0
    payload = {
        "timestamp": str(row.get("open_ts") or row.get("timestamp") or _safe_timestamp()),
        "rsi": features.get("rsi"),
        "atr": features.get("atr"),
        "volume_ratio": features.get("volume_ratio"),
        "lstm_signal": features.get("lstm_signal"),
        "sentimento": features.get("sentimento"),
        "estrategia": features.get("estrategia"),
        "tendencia": features.get("tendencia"),
        "volatilidade": features.get("volatilidade"),
        "score_entrada": features.get("score_entrada"),
        "distancia_resistencia": features.get("distancia_resistencia"),
        "distancia_suporte": features.get("distancia_suporte"),
        "inclinacao_ema50": features.get("inclinacao_ema50"),
        "inclinacao_ema200": features.get("inclinacao_ema200"),
        "atr_normalizado": features.get("atr_normalizado"),
        "posicao_bollinger": features.get("posicao_bollinger"),
        "candles_desde_breakout": features.get("candles_desde_breakout"),
        "confluencia_sinais": features.get("confluencia_sinais"),
        "resultado": resultado,
        "pnl_abs": pnl_abs,
        "lucro": pnl_abs if pnl_abs > 0 else 0.0,
        "prejuizo": abs(pnl_abs) if pnl_abs < 0 else 0.0,
        "drawdown": 0.0,
    }
    return payload


def importar_trades_do_banco_para_trade_quality(limit: int | None = None) -> int:
    rows = _load_trade_rows_from_main_db(limit=limit)
    if not rows:
        print("Trades importados: 0")
        return 0

    existing_signatures = _load_existing_trade_quality_signatures()
    imported = 0
    for row in rows:
        try:
            payload = _build_trade_quality_payload_from_db_row(row)
            if not payload:
                continue
            signature = _trade_quality_signature(payload)
            if signature in existing_signatures:
                continue
            if registrar_trade_quality_fechado(payload):
                existing_signatures.add(signature)
                imported += 1
        except Exception as e:
            logger.warning(
                "Falha ao importar trade_id=%s para Trade Quality: %s",
                row.get("trade_id"),
                e,
            )

    print(f"Trades importados: {imported}")
    return imported


def reconciliar_dataset_trade_quality_com_banco() -> int:
    dataset = carregar_dataset()
    if dataset.empty:
        return 0

    trade_index = _build_main_db_trade_index()
    if not trade_index:
        return 0

    updated = 0
    with _LOCK:
        _ensure_storage()
        with _quality_connect() as conn:
            cursor = conn.cursor()
            for _, row in dataset.iterrows():
                sample_id = row.get("id")
                timestamp = str(row.get("timestamp") or "").strip()
                if not sample_id or not timestamp:
                    continue
                trade = trade_index.get(timestamp)
                if not trade:
                    continue

                pnl_abs = _to_float(trade.get("profit_loss"), 0.0) or 0.0
                lucro = pnl_abs if pnl_abs > 0 else 0.0
                prejuizo = abs(pnl_abs) if pnl_abs < 0 else 0.0
                cursor.execute(
                    _quality_sql("""
                    UPDATE trade_quality_samples
                    SET lucro = ?,
                        prejuizo = ?,
                        pnl_abs = ?,
                        drawdown = COALESCE(drawdown, 0.0)
                    WHERE id = ?
                    """),
                    (lucro, prejuizo, pnl_abs, int(sample_id)),
                )
                updated += 1
            conn.commit()

    if _CSV_PATH.exists():
        try:
            refreshed = carregar_dataset()
            refreshed.to_csv(_CSV_PATH, index=False)
        except Exception as e:
            logger.warning("Falha ao sincronizar CSV apos reconciliacao do Trade Quality: %s", e)
    return updated


def treinar_modelo_contexto_recente(view_name: str = "trades_clean_recent_350") -> dict:
    rows = _load_clean_trade_rows(view_name=view_name)
    if not rows:
        return {
            "trained": False,
            "motivo": "dataset_recente_vazio",
            "samples": 0,
        }

    enriched_rows = []
    context_coverage = 0
    market_cache: dict[str, pd.DataFrame | None] = {}
    main_index = _build_main_db_trade_index()
    rows_sorted = sorted(
        rows,
        key=lambda item: _parse_timestamp_any(item.get("timestamp_entrada")) or pd.Timestamp.min,
    )
    historical_snapshot = []
    for row in rows_sorted:
        entry_ts = _parse_timestamp_any(row.get("timestamp_entrada"))
        symbol = str(row.get("ativo") or "").upper().strip()
        if not symbol:
            continue

        if symbol not in market_cache:
            market_cache[symbol] = _load_full_market_context_from_csv_recursive(symbol)
        market_df = market_cache.get(symbol)
        df_ctx = None
        if market_df is not None and not market_df.empty and entry_ts is not None:
            df_ctx = market_df[market_df["time"] <= entry_ts].tail(220).copy()
        elif market_df is not None and not market_df.empty:
            df_ctx = market_df.tail(220).copy()
        context_features = {}
        if df_ctx is not None and not df_ctx.empty:
            context_features = extrair_features_contexto(df_ctx, preco_entrada=row.get("preco_entrada"))
            if context_features:
                context_coverage += 1

        entry_dt = _parse_timestamp_any(row.get("timestamp_entrada"))
        trade_key = str(row.get("timestamp_entrada") or "").strip()
        main_row = main_index.get(trade_key, {})
        merged_trade = {**main_row, **row}
        decision_features = extrair_features_decisao(
            merged_trade,
            historical_snapshot=historical_snapshot,
            context_features=context_features,
        )
        enriched_rows.append(
            {
                "ativo": symbol,
                "lado": str(row.get("lado") or "").strip().lower(),
                "strategy": _normalize_strategy(row.get("strategy")),
                "decision_source": str(row.get("decision_source") or "desconhecido").strip().lower() or "desconhecido",
                "preco_entrada": _to_float(row.get("preco_entrada"), 0.0) or 0.0,
                "quantidade": _to_float(row.get("quantidade"), 0.0) or 0.0,
                "hora_entrada": int(entry_dt.hour) if entry_dt is not None else -1,
                "dia_semana_entrada": int(entry_dt.dayofweek) if entry_dt is not None else -1,
                "resultado": _normalize_binary_target(row.get("resultado")),
                "pnl": _to_float(row.get("pnl"), 0.0) or 0.0,
                **decision_features,
                **{key: context_features.get(key, 0.0) for key in _CONTEXT_FEATURE_COLUMNS if key not in {
                    "ativo", "lado", "strategy", "decision_source", "preco_entrada", "quantidade", "hora_entrada", "dia_semana_entrada"
                }},
            }
        )
        historical_snapshot.append({"resultado": _normalize_binary_target(row.get("resultado"))})

    dataset = pd.DataFrame(enriched_rows)
    if dataset.empty or len(dataset) < 50:
        return {
            "trained": False,
            "motivo": f"amostras_insuficientes_{len(dataset)}",
            "samples": int(len(dataset)),
        }

    X = dataset[_TRAINING_FEATURE_COLUMNS].copy()
    y = dataset["resultado"].astype(int)
    X_encoded = pd.get_dummies(
        X,
        columns=["ativo", "lado", "strategy", "decision_source", "tipo_estrategia", "tipo_ordem"],
        dummy_na=False,
    )

    X_train, X_test, y_train, y_test, idx_train, idx_test = train_test_split(
        X_encoded,
        y,
        dataset.index,
        test_size=0.2,
        random_state=42,
        stratify=y,
    )

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=6,
        min_samples_leaf=5,
        random_state=42,
    )
    model.fit(X_train, y_train)
    y_prob = model.predict_proba(X_test)[:, 1]

    def _profit_factor(frame: pd.DataFrame) -> float:
        wins = frame.loc[frame["pnl"] > 0, "pnl"].sum()
        losses = frame.loc[frame["pnl"] < 0, "pnl"].sum()
        return float(wins / abs(losses)) if losses < 0 else (float("inf") if wins > 0 else 0.0)

    def _ev(frame: pd.DataFrame) -> float:
        if frame.empty:
            return 0.0
        win_mask = frame["resultado"] == 1
        loss_mask = frame["resultado"] == 0
        win_rate = float(win_mask.mean()) if len(frame) else 0.0
        loss_rate = float(loss_mask.mean()) if len(frame) else 0.0
        lucro_medio = float(frame.loc[win_mask, "pnl"].mean()) if win_mask.any() else 0.0
        perda_media = float(abs(frame.loc[loss_mask, "pnl"].mean())) if loss_mask.any() else 0.0
        return float((win_rate * lucro_medio) - (loss_rate * perda_media))

    def _metrics(frame: pd.DataFrame) -> dict:
        if frame.empty:
            return {"count": 0, "win_rate": 0.0, "lucro_total": 0.0, "profit_factor": 0.0, "ev": 0.0}
        return {
            "count": int(len(frame)),
            "win_rate": float(frame["resultado"].mean()),
            "lucro_total": float(frame["pnl"].sum()),
            "profit_factor": _profit_factor(frame),
            "ev": _ev(frame),
        }

    auc = float(roc_auc_score(y_test, y_prob))
    brier = calcular_brier_score(y_test.tolist(), y_prob.tolist())
    prob_stats = {
        "min": float(np.min(y_prob)),
        "max": float(np.max(y_prob)),
        "mean": float(np.mean(y_prob)),
        "std": float(np.std(y_prob)),
    }

    base_test = dataset.loc[idx_test].copy()
    base_test["probabilidade"] = y_prob
    baseline = _metrics(base_test)

    thresholds = [0.35, 0.4, 0.45, 0.5]
    simulacao = {}
    for threshold in thresholds:
        kept = base_test[base_test["probabilidade"] >= threshold].copy()
        simulacao[str(threshold)] = _metrics(kept)

    importances = pd.Series(model.feature_importances_, index=X_encoded.columns).sort_values(ascending=False)
    top_features = [
        {"feature": str(name), "importance": float(value)}
        for name, value in importances.head(15).items()
    ]

    bundle = {
        "model": model,
        "feature_columns": list(X_encoded.columns),
        "context_feature_columns": list(_CONTEXT_FEATURE_COLUMNS),
        "decision_feature_columns": list(_DECISION_FEATURE_COLUMNS),
        "trained_at": datetime.utcnow().isoformat(),
        "dataset_source": view_name,
        "sample_count": int(len(dataset)),
        "target_balance": {str(k): int(v) for k, v in y.value_counts().sort_index().to_dict().items()},
        "model_variant": "context_decision_recent_350",
    }
    joblib.dump(bundle, _MODEL_CONTEXT_PATH)

    report = {
        "dataset": view_name,
        "dataset_size": int(len(dataset)),
        "context_coverage": round(float(context_coverage / max(len(dataset), 1)), 4),
        "feature_cols": list(_TRAINING_FEATURE_COLUMNS),
        "auc": auc,
        "brier_score": brier,
        "probabilidade": prob_stats,
        "baseline_test": baseline,
        "simulacao": simulacao,
        "top_features": top_features,
        "model_path": str(_MODEL_CONTEXT_PATH),
    }
    _TRAINING_REPORT_CONTEXT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "trained": True,
        "samples": int(len(dataset)),
        "auc": auc,
        "brier_score": brier,
        "probabilidade": prob_stats,
        "baseline_test": baseline,
        "simulacao": simulacao,
        "top_features": top_features,
        "context_coverage": round(float(context_coverage / max(len(dataset), 1)), 4),
        "model_path": str(_MODEL_CONTEXT_PATH),
        "report_path": str(_TRAINING_REPORT_CONTEXT_PATH),
    }
