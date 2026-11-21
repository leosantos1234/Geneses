import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import uuid  # ✅ Adiciona esta linha

from genesis_runtime import database_backend, database_path, database_url

try:
    import psycopg
except ImportError:  # pragma: no cover - usado somente quando PostgreSQL está configurado
    psycopg = None

DB_PATH = database_path()
CURRENT_MODEL_VERSION = os.getenv("MODEL_VERSION", "v2_btc_protecao_antecipada")


def _postgres_url():
    return database_url() or ""


def _using_postgres():
    return database_backend() == "postgres"


def _connect_database():
    """Abre exclusivamente o banco oficial PostgreSQL."""
    if not _using_postgres():
        raise RuntimeError("SQLite desativado: configure GENESIS_DATABASE_BACKEND=postgres.")
    if psycopg is None:
        raise RuntimeError("GENESIS_DATABASE_URL configurada, mas psycopg não está instalado.")
    return psycopg.connect(
        _postgres_url(),
        connect_timeout=5,
        options="-c statement_timeout=5000 -c lock_timeout=1000",
    )


def _sql(query):
    return query.replace("?", "%s") if _using_postgres() else query


def _normalize_optional_number(value):
    if value in (None, "", "None"):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if number == 0.0:
        return None
    return number


def _safe_float(value, default=None):
    try:
        if value in (None, "", "None"):
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value, default=0):
    try:
        if value in (None, "", "None"):
            return default
        return int(value)
    except Exception:
        return default


def _ms_to_iso8601(value):
    value_int = _safe_int(value, 0)
    if value_int <= 0:
        return None
    try:
        if value_int > 10_000_000_000:
            dt = datetime.fromtimestamp(value_int / 1000.0, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(value_int, tz=timezone.utc)
        return dt.replace(tzinfo=None).isoformat()
    except Exception:
        return None


def _iso_to_ms(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _create_bybit_history_session():
    from pybit.unified_trading import HTTP
    api_key = os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("BYBIT_API_KEY/BYBIT_API_SECRET nao configuradas.")
    testnet = str(os.getenv("BYBIT_TESTNET", "False")).strip().lower() in ("1", "true", "yes")
    return HTTP(api_key=api_key, api_secret=api_secret, testnet=testnet)


def _normalize_bybit_side(value):
    raw = str(value or "").strip().lower()
    if raw in ("buy", "sell"):
        return raw
    return raw or None


def _current_model_version():
    return str(CURRENT_MODEL_VERSION or "v2_btc_protecao_antecipada").strip() or "v2_btc_protecao_antecipada"


def _ensure_trade_import_columns(cursor):
    cursor.execute("PRAGMA table_info(trades)")
    existentes = {row[1] for row in cursor.fetchall()}
    novas_colunas = {
        "order_id": "TEXT",
        "exec_id": "TEXT",
        "quantity": "REAL",
        "exec_value": "REAL",
        "exec_fee": "REAL",
        "import_source": "TEXT",
        "model_version": "TEXT",
    }
    for coluna, tipo in novas_colunas.items():
        if coluna not in existentes:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {coluna} {tipo}")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_exec_id_unique "
        "ON trades(exec_id) WHERE exec_id IS NOT NULL"
    )


def _map_execution_to_trade_row(execucao):
    timestamp_iso = _ms_to_iso8601(execucao.get("execTime")) or datetime.utcnow().isoformat()
    exec_id = str(execucao.get("execId") or "").strip()
    order_id = str(execucao.get("orderId") or "").strip()
    pnl = _safe_float(execucao.get("closedPnl"), None)
    return {
        "trade_id": exec_id or str(uuid.uuid4()),
        "timestamp": timestamp_iso,
        "symbol": str(execucao.get("symbol") or "").strip().upper(),
        "side": _normalize_bybit_side(execucao.get("side")),
        "entry_price": _safe_float(execucao.get("execPrice"), None),
        "exit_price": None,
        "profit_loss": pnl,
        "strategy": "bybit_execution_import",
        "decision_source": "bybit_execution_import",
        "entry_score": 0.0,
        "sentiment_score": None,
        "stop_loss": None,
        "take_profit": None,
        "trailing_stop": None,
        "volatilidade_histÃ³rica": None,
        "liquidez_mercado": None,
        "rsi_entrada": None,
        "macd_entrada": None,
        "sma_entrada": None,
        "atr_entrada": None,
        "volume_entrada": None,
        "sentimento_entrada": None,
        "ajuste_manual_stop_loss": None,
        "ajuste_manual_take_profit": None,
        "trailing_stop_inicial": None,
        "trailing_stop_final": None,
        "backtest_result": None,
        "spread": None,
        "profundidade_livro": None,
        "spread_pct": None,
        "execution_latency_ms": None,
        "api_ping_ms": None,
        "slippage_pct": None,
        "slippage_abs": None,
        "estrategia_utilizada": "bybit_execution_import",
        "risco_por_operacao": None,
        "timestamp_decisao_ia": timestamp_iso,
        "regime": None,
        "tendencia": None,
        "consenso": None,
        "consenso_score": 0.0,
        "consenso_razao": None,
        "decisao_lstm": None,
        "open_ts": timestamp_iso,
        "close_ts": None,
        "status": "importado_bybit",
        "order_id": order_id or None,
        "exec_id": exec_id or None,
        "quantity": _safe_float(execucao.get("execQty"), None),
        "exec_value": _safe_float(execucao.get("execValue"), None),
        "exec_fee": _safe_float(execucao.get("execFee"), None),
        "import_source": "bybit_execution_list",
        "model_version": _current_model_version(),
    }


def _map_closed_pnl_to_trade_row(item):
    created_iso = _ms_to_iso8601(item.get("createdTime")) or datetime.utcnow().isoformat()
    updated_iso = _ms_to_iso8601(item.get("updatedTime")) or created_iso
    order_id = str(item.get("orderId") or "").strip()
    symbol = str(item.get("symbol") or "").strip().upper()
    side = _normalize_bybit_side(item.get("side"))
    entry_price = _safe_float(item.get("avgEntryPrice"), None)
    exit_price = _safe_float(item.get("avgExitPrice"), None)
    closed_pnl = _safe_float(item.get("closedPnl"), None)
    qty = _safe_float(item.get("qty") or item.get("closedSize"), None)
    fee_total = (_safe_float(item.get("openFee"), 0.0) or 0.0) + (_safe_float(item.get("closeFee"), 0.0) or 0.0)
    return {
        "trade_id": f"bybit_closed_pnl:{order_id}" if order_id else str(uuid.uuid4()),
        "timestamp": created_iso,
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "profit_loss": closed_pnl,
        "strategy": "bybit_closed_pnl_import",
        "decision_source": "bybit_closed_pnl_import",
        "open_ts": created_iso,
        "close_ts": updated_iso,
        "status": "fechado",
        "order_id": order_id or None,
        "exec_id": None,
        "quantity": qty,
        "exec_value": _safe_float(item.get("cumEntryValue"), None),
        "exec_fee": fee_total,
        "import_source": "bybit_closed_pnl",
        "model_version": _current_model_version(),
    }

def salvar_trade_sqlite(trade_data):
    """
    Salva os dados de um trade na tabela 'trades', evitando duplicações
    por conteúdo (mesmo ativo, preço e estratégia em curto intervalo).
    """
    import sqlite3
    import uuid
    import logging
    from datetime import datetime

    campos = [
        "trade_id", "decision_id", "timestamp", "symbol", "side", "entry_price", "exit_price",
        "profit_loss", "strategy", "decision_source",
        "entry_score", "sentiment_score", "stop_loss", "take_profit", "trailing_stop",
        "volatilidade_histórica", "liquidez_mercado", "rsi_entrada", "macd_entrada",
        "sma_entrada", "atr_entrada", "volume_entrada", "sentimento_entrada",
        "ajuste_manual_stop_loss", "ajuste_manual_take_profit", "trailing_stop_inicial",
        "trailing_stop_final", "backtest_result", "spread", "profundidade_livro",
        "spread_pct", "execution_latency_ms", "api_ping_ms", "slippage_pct", "slippage_abs",
        "estrategia_utilizada", "risco_por_operacao", "timestamp_decisao_ia",
        "regime", "tendencia", "consenso", "consenso_score", "consenso_razao", "decisao_lstm",
        "open_ts", "close_ts", "status", "order_id", "exec_id", "quantity",
        "exec_value", "exec_fee", "import_source", "model_version"
    ]

    trade_id = trade_data.get("trade_id") or str(uuid.uuid4())
    trade_data["trade_id"] = trade_id
    trade_data["take_profit"] = _normalize_optional_number(trade_data.get("take_profit"))
    trade_data["trailing_stop"] = _normalize_optional_number(trade_data.get("trailing_stop"))
    trade_data["trailing_stop_inicial"] = _normalize_optional_number(trade_data.get("trailing_stop_inicial"))
    trade_data["trailing_stop_final"] = _normalize_optional_number(trade_data.get("trailing_stop_final"))
    trade_data["entry_score"] = trade_data.get("entry_score", 0.0)
    trade_data["consenso_score"] = trade_data.get("consenso_score", 0.0)
    trade_data["atr_entrada"] = trade_data.get("atr_entrada", 0.0)
    trade_data["timestamp_decisao_ia"] = trade_data.get("timestamp_decisao_ia") or datetime.utcnow().isoformat()
    trade_data["open_ts"] = trade_data.get("open_ts") or trade_data.get("timestamp") or datetime.utcnow().isoformat()
    if trade_data.get("close_ts"):
        trade_data["status"] = trade_data.get("status") or "fechado"
    else:
        trade_data["status"] = trade_data.get("status") or "aberto"
    trade_data["model_version"] = trade_data.get("model_version") or _current_model_version()
    valores = [trade_data.get(c) for c in campos]

    conn = _connect_database()
    cursor = conn.cursor()

    # 🔒 Evita duplicações por conteúdo (mesmo trade recente)
    duplicate_sql = """
        SELECT COUNT(*) FROM trades
        WHERE symbol = ?
        AND side = ?
        AND entry_price = ?
        AND take_profit = ?
        AND stop_loss = ?
        AND strategy = ?
        AND datetime(timestamp) >= datetime('now', '-10 minutes')
    """
    if _using_postgres():
        duplicate_sql = duplicate_sql.replace(
            "datetime(timestamp) >= datetime('now', '-10 minutes')",
            "timestamp::timestamp >= CURRENT_TIMESTAMP - INTERVAL '10 minutes'",
        )
    cursor.execute(_sql(duplicate_sql), (
        trade_data.get("symbol"),
        trade_data.get("side"),
        trade_data.get("entry_price"),
        trade_data.get("take_profit"),
        trade_data.get("stop_loss"),
        trade_data.get("strategy"),
    ))

    duplicado = cursor.fetchone()[0]
    if duplicado > 0:
        logging.warning(f"⚠️ Trade duplicado detectado — ignorando salvamento ({trade_data.get('symbol')} | {trade_data.get('strategy')})")
        conn.close()
        return

    # 🔍 Evita duplicação por trade_id (caso extremo)
    cursor.execute(_sql("SELECT 1 FROM trades WHERE trade_id=?"), (trade_id,))
    if cursor.fetchone():
        logging.warning(f"⚠️ Trade {trade_id} já existente — ignorando duplicação.")
        conn.close()
        return

    # ✅ Insere trade normalmente
    insert_sql = f"""
        INSERT INTO trades ({', '.join(campos)})
        VALUES ({', '.join(['?'] * len(campos))})
    """
    try:
        cursor.execute(_sql(insert_sql), valores)
    except sqlite3.OperationalError as e:
        if "has no column named" not in str(e):
            raise
        conn.close()
        inicializar_banco()
        conn = _connect_database()
        cursor = conn.cursor()
        cursor.execute(_sql(insert_sql), valores)

    conn.commit()
    conn.close()
    logging.info(f"✅ Trade salvo com sucesso: {trade_id}")


def atualizar_trade_sqlite(
    trade_id,
    exit_price=None,
    profit_loss=None,
    symbol=None,
    entry_price=None,
    close_ts=None,
    trailing_stop_final=None,
    status="fechado",
):
    """
    Atualiza um trade existente com dados de saída (exit_price / profit_loss).
    Se trade_id não for encontrado, tenta atualizar o último trade aberto do símbolo.
    """
    import sqlite3
    import logging

    if (
        exit_price is None
        and profit_loss is None
        and close_ts is None
        and trailing_stop_final is None
        and status is None
    ):
        return

    conn = _connect_database()
    cursor = conn.cursor()
    close_ts = close_ts or datetime.utcnow().isoformat()
    trailing_stop_final = _normalize_optional_number(trailing_stop_final)

    def _run_update(where_sql, where_args):
        cursor.execute(
            _sql(f"""
            UPDATE trades
            SET
                exit_price = COALESCE(?, exit_price),
                profit_loss = COALESCE(?, profit_loss),
                close_ts = COALESCE(?, close_ts),
                trailing_stop_final = COALESCE(?, trailing_stop_final),
                status = COALESCE(?, status)
            WHERE {where_sql}
            """),
            (exit_price, profit_loss, close_ts, trailing_stop_final, status, *where_args),
        )

    # Primeiro tenta por trade_id (mais preciso).
    if trade_id:
        cursor.execute(_sql("SELECT id FROM trades WHERE trade_id=?"), (trade_id,))
        row = cursor.fetchone()
        if row:
            _run_update("trade_id=?", (trade_id,))
            conn.commit()
            conn.close()
            logging.info(f"Trade atualizado (exit) via trade_id={trade_id}")
            return

    # Fallback: último trade aberto do símbolo (exit_price ainda vazio).
    if symbol:
        cursor.execute(
            _sql("""
            SELECT id FROM trades
            WHERE symbol = ? AND (exit_price IS NULL OR exit_price = 0)
            ORDER BY id DESC LIMIT 1
            """),
            (symbol,),
        )
        row = cursor.fetchone()
        if row:
            trade_row_id = row[0]
            _run_update("id=?", (trade_row_id,))
            conn.commit()
            conn.close()
            logging.info(f"Trade atualizado (exit) via fallback symbol={symbol}")
            return

    conn.close()
    logging.warning(
        f"Nenhum trade encontrado para atualizar (trade_id={trade_id}, symbol={symbol})."
    )





def _ensure_postgres_operational_schema():
    """Create the additive operational schema required by worker startup."""
    conn = _connect_database()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id BIGSERIAL PRIMARY KEY, trade_id TEXT UNIQUE, timestamp TEXT,
                symbol TEXT, side TEXT, entry_price DOUBLE PRECISION,
                exit_price DOUBLE PRECISION, profit_loss DOUBLE PRECISION,
                strategy TEXT, decision_source TEXT, entry_score DOUBLE PRECISION,
                sentiment_score DOUBLE PRECISION, stop_loss DOUBLE PRECISION,
                take_profit DOUBLE PRECISION, trailing_stop DOUBLE PRECISION,
                volatilidade_histÃ³rica DOUBLE PRECISION, liquidez_mercado DOUBLE PRECISION,
                rsi_entrada DOUBLE PRECISION, macd_entrada DOUBLE PRECISION,
                sma_entrada DOUBLE PRECISION, atr_entrada DOUBLE PRECISION,
                volume_entrada DOUBLE PRECISION, sentimento_entrada DOUBLE PRECISION,
                ajuste_manual_stop_loss DOUBLE PRECISION, ajuste_manual_take_profit DOUBLE PRECISION,
                trailing_stop_inicial DOUBLE PRECISION, trailing_stop_final DOUBLE PRECISION,
                backtest_result DOUBLE PRECISION, spread DOUBLE PRECISION,
                profundidade_livro DOUBLE PRECISION, spread_pct DOUBLE PRECISION,
                execution_latency_ms DOUBLE PRECISION, api_ping_ms DOUBLE PRECISION,
                slippage_pct DOUBLE PRECISION, slippage_abs DOUBLE PRECISION,
                estrategia_utilizada TEXT, risco_por_operacao DOUBLE PRECISION,
                timestamp_decisao_ia TEXT, regime TEXT, tendencia TEXT, consenso TEXT,
                consenso_score DOUBLE PRECISION, consenso_razao TEXT, decisao_lstm TEXT,
                open_ts TEXT, close_ts TEXT, status TEXT, model_version TEXT,
                order_id TEXT, exec_id TEXT, quantity DOUBLE PRECISION,
                exec_value DOUBLE PRECISION, exec_fee DOUBLE PRECISION,
                import_source TEXT, decision_id UUID
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS predictions_lstm (
                id BIGSERIAL PRIMARY KEY, trade_id TEXT, symbol TEXT, model_name TEXT,
                timestamp TEXT, predicted_price DOUBLE PRECISION, real_price DOUBLE PRECISION,
                direction_predicted TEXT, direction_real TEXT, error_percent DOUBLE PRECISION,
                confidence_score DOUBLE PRECISION, window_size INTEGER, sequence_input TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS log_events (
                id BIGSERIAL PRIMARY KEY, ts TEXT, level TEXT, logger TEXT,
                strategy TEXT, symbol TEXT, message TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decision_trace (
                decision_id UUID PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), symbol TEXT, timeframe TEXT,
                strategy TEXT, environment TEXT, mode TEXT,
                final_status TEXT NOT NULL DEFAULT 'IN_PROGRESS', trade_id TEXT,
                order_id TEXT, position_id TEXT, schema_version TEXT NOT NULL DEFAULT '1.0',
                decision_context JSONB NOT NULL DEFAULT '{}'::jsonb, config_fingerprint TEXT,
                code_version TEXT, strategy_version TEXT, model_version TEXT, reason_code TEXT,
                reason_message TEXT, legacy BOOLEAN NOT NULL DEFAULT FALSE,
                lineage_quality TEXT NOT NULL DEFAULT 'complete'
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decision_events (
                event_id BIGSERIAL PRIMARY KEY,
                decision_id UUID NOT NULL REFERENCES decision_trace(decision_id),
                event_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(), stage TEXT NOT NULL,
                event_type TEXT NOT NULL, component TEXT NOT NULL, status TEXT NOT NULL,
                reason_code TEXT, duration_ms DOUBLE PRECISION,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                schema_version TEXT NOT NULL DEFAULT '1.0'
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS genesis_migration_runs (
                migration_key TEXT PRIMARY KEY, source_path TEXT NOT NULL,
                completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), status TEXT NOT NULL,
                details JSONB NOT NULL DEFAULT '{}'::jsonb
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS commander_decisions (
                commander_decision_id TEXT PRIMARY KEY, timestamp_utc TIMESTAMPTZ NOT NULL,
                symbol TEXT, decision_type TEXT NOT NULL, strategy_id TEXT,
                strategy_name TEXT, confidence TEXT, reference_price NUMERIC,
                market_regime TEXT, commander_version TEXT NOT NULL,
                source_context_id TEXT, decision_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                rationale_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN'
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS breakout_evaluations (
                breakout_evaluation_id TEXT PRIMARY KEY, timestamp_utc TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, regime TEXT,
                direction_candidate TEXT, stage_results JSONB NOT NULL DEFAULT '{}'::jsonb,
                primary_blocker TEXT, failed_stages JSONB NOT NULL DEFAULT '[]'::jsonb,
                raw_breakout BOOLEAN NOT NULL DEFAULT FALSE, final_state TEXT NOT NULL,
                commander_decision_id TEXT, risk_status TEXT, risk_reason TEXT,
                risk_evaluated_at TIMESTAMPTZ, paper_order_id TEXT, paper_fill_id TEXT,
                paper_effect_id TEXT, record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), candle_open_time TIMESTAMPTZ,
                candle_close_time TIMESTAMPTZ, candle_closed BOOLEAN, analysis_key TEXT,
                evaluation_context JSONB NOT NULL DEFAULT '{}'::jsonb, run_id TEXT,
                commander_invoked BOOLEAN NOT NULL DEFAULT FALSE,
                feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
                breakout_timeframe TEXT, breakout_candle_open_time TIMESTAMPTZ,
                breakout_candle_close_time TIMESTAMPTZ, breakout_candle_closed BOOLEAN,
                canonical_regime_timeframe TEXT, canonical_regime_candle_open_time TIMESTAMPTZ,
                canonical_regime_candle_close_time TIMESTAMPTZ
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mr_fsm_state (
                symbol TEXT PRIMARY KEY, updated_at TEXT NOT NULL, state_json TEXT NOT NULL
            )
        """)
        # Additive observability/RAG tables used by the production worker.
        # Keep these idempotent so existing Render databases can be upgraded
        # safely during the normal worker startup.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rag_records (
                rag_record_id UUID PRIMARY KEY, source_type TEXT NOT NULL,
                source_id TEXT NOT NULL, symbol TEXT, timestamp_utc TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), tags TEXT[] DEFAULT ARRAY[]::TEXT[],
                summary TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
                metadata JSONB DEFAULT '{}'::jsonb, schema_version TEXT NOT NULL DEFAULT '1.0'
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS canonical_market_candles_1m (
                symbol TEXT NOT NULL, exchange TEXT NOT NULL, timeframe TEXT NOT NULL CHECK (timeframe = '1m'),
                candle_open_time TIMESTAMPTZ NOT NULL, candle_close_time TIMESTAMPTZ NOT NULL,
                open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume NUMERIC, turnover NUMERIC,
                closed BOOLEAN NOT NULL CHECK (closed = TRUE), record_provenance TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, exchange, timeframe, candle_open_time)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analytical_candle_telemetry (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, candle_open_time TIMESTAMPTZ NOT NULL,
                candle_close_time TIMESTAMPTZ NOT NULL, open NUMERIC, high NUMERIC, low NUMERIC,
                close NUMERIC, volume NUMERIC, atr NUMERIC, atr_percent NUMERIC, volume_ratio NUMERIC,
                body_fraction NUMERIC, regime INTEGER CHECK (regime IN (1,2,3,4)), winning_rule TEXT,
                candle_closed BOOLEAN NOT NULL, record_provenance TEXT NOT NULL,
                feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, timeframe, candle_open_time)
            )
        """)
        # Prospective analytical outcomes are observability-only.  Keep this
        # additive and idempotent so older Render databases receive the same
        # activation watermark expected by analytical_telemetry.py.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analytical_event_outcomes (
                id TEXT PRIMARY KEY, event_type TEXT NOT NULL, event_id TEXT NOT NULL,
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                event_candle_open_time TIMESTAMPTZ NOT NULL, event_candle_close_time TIMESTAMPTZ,
                direction TEXT, regime INTEGER, blocker TEXT,
                feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
                forward_return_1 NUMERIC, forward_return_3 NUMERIC, forward_return_6 NUMERIC,
                forward_return_12 NUMERIC, mfe_pct_1 NUMERIC, mae_pct_1 NUMERIC,
                mfe_pct_3 NUMERIC, mae_pct_3 NUMERIC, mfe_pct_6 NUMERIC, mae_pct_6 NUMERIC,
                mfe_pct_12 NUMERIC, mae_pct_12 NUMERIC,
                forward_return_1_available_at TIMESTAMPTZ, forward_return_3_available_at TIMESTAMPTZ,
                forward_return_6_available_at TIMESTAMPTZ, forward_return_12_available_at TIMESTAMPTZ,
                outcome_class TEXT, available_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (event_type, event_id),
                CHECK (direction IS NULL OR upper(direction) IN ('BUY','SELL','LONG','SHORT','UNKNOWN')),
                CHECK (outcome_class IS NULL OR outcome_class IN
                    ('PROTECTED_CAPITAL','MISSED_OPPORTUNITY','NEUTRAL','INSUFFICIENT_DATA'))
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS analytical_event_outcomes_symbol_idx ON analytical_event_outcomes(symbol, event_candle_open_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS analytical_event_outcomes_blocker_idx ON analytical_event_outcomes(symbol, blocker, regime)")
        cursor.execute("CREATE INDEX IF NOT EXISTS analytical_event_outcomes_available_idx ON analytical_event_outcomes(available_at)")
        cursor.execute("""
            INSERT INTO genesis_migration_runs(migration_key, source_path, status, details)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (migration_key) DO UPDATE
            SET completed_at=NOW(), status=EXCLUDED.status, details=EXCLUDED.details
        """, (
            "analytical-outcomes-v3",
            "Robo/ops/migrations/012_analytical_outcomes_v3.sql",
            "completed",
            '{"additive":true,"prospective_only":true,"historical_backfill":false,"commander_authority":"NONE"}',
        ))
        cursor.execute("CREATE INDEX IF NOT EXISTS rag_records_symbol_idx ON rag_records(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS canonical_market_candles_1m_close_idx ON canonical_market_candles_1m(symbol, candle_close_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS analytical_candle_telemetry_close_idx ON analytical_candle_telemetry(symbol, timeframe, candle_close_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS trades_decision_id_idx ON trades(decision_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS decision_trace_symbol_created_idx ON decision_trace(symbol, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS decision_trace_trade_idx ON decision_trace(trade_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS log_events_ts_idx ON log_events(ts)")
        cursor.execute("CREATE INDEX IF NOT EXISTS log_events_symbol_ts_idx ON log_events(symbol, ts DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS decision_events_decision_ts_idx ON decision_events(decision_id, event_ts, event_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS commander_decisions_context_idx ON commander_decisions(source_context_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS breakout_evaluations_time_idx ON breakout_evaluations(timestamp_utc, symbol, direction_candidate)")
        cursor.execute("CREATE INDEX IF NOT EXISTS breakout_evaluations_blocker_idx ON breakout_evaluations(symbol, primary_blocker, timestamp_utc)")
        cursor.execute("CREATE INDEX IF NOT EXISTS breakout_evaluations_commander_idx ON breakout_evaluations(commander_decision_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS breakout_evaluations_1m_lineage_idx ON breakout_evaluations(symbol, breakout_timeframe, breakout_candle_open_time)")
        conn.commit()
    finally:
        conn.close()


def inicializar_banco():
    if _using_postgres():
        _ensure_postgres_operational_schema()
        return
        conn = _connect_database()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN ('trades', 'predictions_lstm')
                """
            )
            tables = {row[0] for row in cursor.fetchall()}
            missing = {'trades', 'predictions_lstm'} - tables
            if missing:
                raise RuntimeError(
                    "PostgreSQL conectado, mas faltam tabelas obrigatórias: "
                    + ", ".join(sorted(missing))
                )
        finally:
            conn.close()
        return

    """
    Cria as tabelas 'trades' e 'predictions_lstm' se não existirem.
    """
    conn = _connect_database()
    cursor = conn.cursor()

    # === TABELA PRINCIPAL DE TRADES ===
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE,
            timestamp TEXT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            exit_price REAL,
            profit_loss REAL,
            strategy TEXT,
            decision_source TEXT,
            entry_score REAL,
            sentiment_score REAL,
            stop_loss REAL,
            take_profit REAL,
            trailing_stop REAL,
            volatilidade_histórica REAL,
            liquidez_mercado REAL,
            rsi_entrada REAL,
            macd_entrada REAL,
            sma_entrada REAL,
            atr_entrada REAL,
            volume_entrada REAL,
            sentimento_entrada REAL,
            ajuste_manual_stop_loss REAL,
            ajuste_manual_take_profit REAL,
            trailing_stop_inicial REAL,
            trailing_stop_final REAL,
            backtest_result REAL,
            spread REAL,
            profundidade_livro REAL,
            spread_pct REAL,
            execution_latency_ms REAL,
            api_ping_ms REAL,
            slippage_pct REAL,
            slippage_abs REAL,
            estrategia_utilizada TEXT,
            risco_por_operacao REAL,
            timestamp_decisao_ia TEXT,
            regime TEXT,
            tendencia TEXT,
            consenso TEXT,
            consenso_score REAL,
            consenso_razao TEXT,
            decisao_lstm TEXT,
            open_ts TEXT,
            close_ts TEXT,
            status TEXT,
            model_version TEXT
        )
    """)

    # Garante colunas novas em bancos existentes sem quebrar o schema atual
    cursor.execute("PRAGMA table_info(trades)")
    existentes = {row[1] for row in cursor.fetchall()}
    novas_colunas = {
        "decision_id": "TEXT",
        "regime": "TEXT",
        "tendencia": "TEXT",
        "consenso": "TEXT",
        "consenso_score": "REAL",
        "consenso_razao": "TEXT",
        "decisao_lstm": "TEXT",
        "open_ts": "TEXT",
        "close_ts": "TEXT",
        "status": "TEXT",
        "spread_pct": "REAL",
        "execution_latency_ms": "REAL",
        "api_ping_ms": "REAL",
        "slippage_pct": "REAL",
        "slippage_abs": "REAL",
        "order_id": "TEXT",
        "exec_id": "TEXT",
        "quantity": "REAL",
        "exec_value": "REAL",
        "exec_fee": "REAL",
        "import_source": "TEXT",
        "model_version": "TEXT",
    }
    for coluna, tipo in novas_colunas.items():
        if coluna not in existentes:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {coluna} {tipo}")
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_exec_id_unique "
        "ON trades(exec_id) WHERE exec_id IS NOT NULL"
    )
    now_iso = datetime.utcnow().isoformat()
    cursor.execute(
        """
        UPDATE trades
        SET model_version = CASE
            WHEN COALESCE(open_ts, timestamp, ?) < ? THEN 'v1'
            ELSE ?
        END
        WHERE model_version IS NULL OR TRIM(model_version) = ''
        """,
        (now_iso, now_iso, _current_model_version()),
    )
    cursor.execute("DROP VIEW IF EXISTS trades_v2")
    version_sql = _current_model_version().replace("'", "''")
    cursor.execute(
        f"""
        CREATE VIEW trades_v2 AS
        SELECT *
        FROM trades
        WHERE model_version = '{version_sql}'
        """
    )

    # === NOVA TABELA DE PREVISÕES LSTM ===
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions_lstm (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            symbol TEXT,
            model_name TEXT,
            timestamp TEXT,
            predicted_price REAL,
            real_price REAL,
            direction_predicted TEXT,
            direction_real TEXT,
            error_percent REAL,
            confidence_score REAL,
            window_size INTEGER,
            sequence_input TEXT,
            FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
        )
    """)

    conn.commit()
    conn.close()


def salvar_previsao_lstm(pred_data):
    """
    Salva uma previsão LSTM vinculada a um trade.
    """
    campos = [
        "trade_id", "symbol", "model_name", "timestamp",
        "predicted_price", "real_price", "direction_predicted",
        "direction_real", "error_percent", "confidence_score",
        "window_size", "sequence_input"
    ]

    valores = [pred_data.get(c) for c in campos]

    conn = _connect_database()
    cursor = conn.cursor()

    cursor.execute(_sql(f"""
        INSERT INTO predictions_lstm ({', '.join(campos)})
        VALUES ({', '.join(['?'] * len(campos))})
    """), valores)

    conn.commit()
    conn.close()
    print(f"✅ Previsão LSTM salva com sucesso para trade_id={pred_data.get('trade_id')}")


def importar_trades_bybit_para_sqlite(symbol=None):
    """
    Importa execucoes historicas da Bybit para o SQLite local sem sobrescrever
    dados existentes. Registros repetidos sao ignorados por exec_id.
    """
    if _using_postgres():
        raise RuntimeError("Importação legada para SQLite desativada: use o PostgreSQL como banco oficial.")
    symbol = str(symbol or "").strip().upper() or None

    inicializar_banco()
    session = _create_bybit_history_session()

    total_trades_recebidos = 0
    total_inseridos = 0
    total_ignorados = 0
    cursor_paginacao = None
    paginas = 0
    max_paginas = max(_safe_int(os.getenv("BYBIT_IMPORT_MAX_PAGES"), 50), 1)
    page_size = min(max(_safe_int(os.getenv("BYBIT_IMPORT_LIMIT"), 100), 1), 100)

    campos = [
        "trade_id",
        "timestamp",
        "symbol",
        "side",
        "entry_price",
        "exit_price",
        "profit_loss",
        "strategy",
        "decision_source",
        "open_ts",
        "close_ts",
        "status",
        "order_id",
        "exec_id",
        "quantity",
        "exec_value",
        "exec_fee",
        "import_source",
        "model_version",
    ]
    insert_sql = f"""
        INSERT OR IGNORE INTO trades ({', '.join(campos)})
        VALUES ({', '.join(['?'] * len(campos))})
    """

    with sqlite3.connect(DB_PATH) as conn:
        db_cursor = conn.cursor()
        _ensure_trade_import_columns(db_cursor)

        while paginas < max_paginas:
            paginas += 1
            params = {
                "category": "linear",
                "limit": page_size,
            }
            if symbol:
                params["symbol"] = symbol
            if cursor_paginacao:
                params["cursor"] = cursor_paginacao

            resposta = session.get_executions(**params)
            if not isinstance(resposta, dict) or resposta.get("retCode") != 0:
                raise RuntimeError(
                    f"Falha ao buscar execucoes Bybit para {symbol or 'ALL'}: "
                    f"{(resposta or {}).get('retMsg') if isinstance(resposta, dict) else resposta}"
                )

            result = resposta.get("result") or {}
            lista = result.get("list") or []
            if not lista:
                break

            for execucao in lista:
                total_trades_recebidos += 1
                row = _map_execution_to_trade_row(execucao)
                valores = [row.get(c) for c in campos]
                db_cursor.execute(insert_sql, valores)
                if db_cursor.rowcount == 1:
                    total_inseridos += 1
                else:
                    total_ignorados += 1

            conn.commit()

            proximo_cursor = str(result.get("nextPageCursor") or "").strip()
            if not proximo_cursor or proximo_cursor == cursor_paginacao:
                break
            cursor_paginacao = proximo_cursor

    logging.info(
        "Importacao Bybit concluida | symbol=%s | recebidos=%s | inseridos=%s | ignorados=%s",
        symbol or "ALL",
        total_trades_recebidos,
        total_inseridos,
        total_ignorados,
    )
    print(f"total_trades_recebidos={total_trades_recebidos}")
    print(f"total_inseridos={total_inseridos}")
    print(f"total_ignorados={total_ignorados}")
    return {
        "symbol": symbol or "ALL",
        "total_trades_recebidos": total_trades_recebidos,
        "total_inseridos": total_inseridos,
        "total_ignorados": total_ignorados,
        "paginas_processadas": paginas,
    }


def importar_closed_pnl_bybit_para_sqlite(symbol=None):
    """
    Importa historico de trades fechados da Bybit via closed pnl.
    Nao sobrescreve dados e ignora duplicados pelo trade_id derivado do orderId.
    """
    if _using_postgres():
        raise RuntimeError("Importação legada para SQLite desativada: use o PostgreSQL como banco oficial.")
    symbol = str(symbol or "").strip().upper() or None

    inicializar_banco()
    session = _create_bybit_history_session()

    total_trades_recebidos = 0
    total_inseridos = 0
    total_ignorados = 0
    paginas = 0
    max_paginas = max(_safe_int(os.getenv("BYBIT_IMPORT_CLOSED_PNL_MAX_PAGES"), 200), 1)
    page_size = min(max(_safe_int(os.getenv("BYBIT_IMPORT_CLOSED_PNL_LIMIT"), 100), 1), 100)
    window_days = min(max(_safe_int(os.getenv("BYBIT_IMPORT_CLOSED_PNL_WINDOW_DAYS"), 7), 1), 7)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    two_years_ago_ms = now_ms - (720 * 24 * 60 * 60 * 1000)
    start_ms = _iso_to_ms(os.getenv("BYBIT_IMPORT_CLOSED_PNL_START", "2024-01-01T00:00:00+00:00"))
    end_ms = _iso_to_ms(os.getenv("BYBIT_IMPORT_CLOSED_PNL_END")) or now_ms
    if start_ms is None:
        start_ms = two_years_ago_ms
    start_ms = max(start_ms, two_years_ago_ms)
    if end_ms < start_ms:
        end_ms = start_ms

    campos = [
        "trade_id",
        "timestamp",
        "symbol",
        "side",
        "entry_price",
        "exit_price",
        "profit_loss",
        "strategy",
        "decision_source",
        "open_ts",
        "close_ts",
        "status",
        "order_id",
        "exec_id",
        "quantity",
        "exec_value",
        "exec_fee",
        "import_source",
        "model_version",
    ]
    insert_sql = f"""
        INSERT OR IGNORE INTO trades ({', '.join(campos)})
        VALUES ({', '.join(['?'] * len(campos))})
    """

    with sqlite3.connect(DB_PATH) as conn:
        db_cursor = conn.cursor()
        _ensure_trade_import_columns(db_cursor)

        current_start = start_ms
        while current_start <= end_ms:
            current_end = min(
                current_start + (window_days * 24 * 60 * 60 * 1000) - 1,
                end_ms,
            )
            cursor_paginacao = None

            while paginas < max_paginas:
                paginas += 1
                params = {
                    "category": "linear",
                    "limit": page_size,
                    "startTime": current_start,
                    "endTime": current_end,
                }
                if symbol:
                    params["symbol"] = symbol
                if cursor_paginacao:
                    params["cursor"] = cursor_paginacao

                resposta = session.get_closed_pnl(**params)
                if not isinstance(resposta, dict) or resposta.get("retCode") != 0:
                    raise RuntimeError(
                        f"Falha ao buscar closed pnl Bybit para {symbol or 'ALL'}: "
                        f"{(resposta or {}).get('retMsg') if isinstance(resposta, dict) else resposta}"
                    )

                result = resposta.get("result") or {}
                lista = result.get("list") or []
                if not lista:
                    break

                for item in lista:
                    total_trades_recebidos += 1
                    row = _map_closed_pnl_to_trade_row(item)
                    valores = [row.get(c) for c in campos]
                    db_cursor.execute(insert_sql, valores)
                    if db_cursor.rowcount == 1:
                        total_inseridos += 1
                    else:
                        total_ignorados += 1

                conn.commit()

                proximo_cursor = str(result.get("nextPageCursor") or "").strip()
                if not proximo_cursor or proximo_cursor == cursor_paginacao:
                    break
                cursor_paginacao = proximo_cursor

            current_start = current_end + 1

    logging.info(
        "Importacao Closed PnL Bybit concluida | symbol=%s | recebidos=%s | inseridos=%s | ignorados=%s",
        symbol or "ALL",
        total_trades_recebidos,
        total_inseridos,
        total_ignorados,
    )
    print(f"closed_pnl_total_trades_recebidos={total_trades_recebidos}")
    print(f"closed_pnl_total_inseridos={total_inseridos}")
    print(f"closed_pnl_total_ignorados={total_ignorados}")
    return {
        "symbol": symbol or "ALL",
        "total_trades_recebidos": total_trades_recebidos,
        "total_inseridos": total_inseridos,
        "total_ignorados": total_ignorados,
        "paginas_processadas": paginas,
    }


