import logging
import os
import re
import sys
import threading
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from genesis_runtime import connect_operational_database, database_backend


def _configure_utf8_stream(stream):
    """Keep console logging readable on Windows terminals."""
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    return stream


def _fix_mojibake(text: str) -> str:
    """Attempt to recover broken UTF-8 text rendered as latin-1 mojibake."""
    if not isinstance(text, str):
        return text
    # Common broken fragments found in Windows console/log output.
    if not any(token in text for token in ("Ã", "ðŸ", "â", "Â", "Ã°Å¸")):
        return text
    try:
        return text.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return text


def _normalize_log_text(text: str) -> str:
    """Repair UTF-8 text that was decoded as latin-1/cp1252."""
    if not isinstance(text, str):
        return text
    markers = tuple(chr(code) for code in (0x00c3, 0x00c2, 0x00e2, 0x00f0, 0x00ef))
    if not any(marker in text for marker in markers):
        return text
    repaired = text
    for _ in range(2):
        try:
            candidate = repaired.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


class SafeUTF8Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _normalize_log_text(super().format(record))


class OperationalDatabaseHandler(logging.Handler):
    IGNORE_PHRASES = [
        "[sim][funil]",
        "status final",
        "relat?rio detalhado da estrat?gia vencedora",
        "relat?rio completo do backtest",
        "backtest",
        "nenhuma ordem aberta",
        "nenhum flag ou ordem ativa",
    ]

    def __init__(self, db_path: str, keywords: str = ""):
        super().__init__()
        self.db_path = db_path
        self.keywords = [k.strip().lower() for k in (keywords or "").split(",") if k.strip()]
        self.capture_all = os.getenv("LOG_DB_CAPTURE_ALL", "0").strip().lower() in ("1", "true", "yes", "y")
        self._lock = threading.Lock()
        self._conn = connect_operational_database()
        if database_backend() == "sqlite":
            self._conn.execute("CREATE TABLE IF NOT EXISTS log_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, logger TEXT, strategy TEXT, symbol TEXT, message TEXT)")
            self._conn.commit()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            msg_lower = msg.lower()
            if any(p in msg_lower for p in self.IGNORE_PHRASES):
                return
            if self.capture_all:
                pass
            elif self.keywords:
                if not any(k in msg_lower for k in self.keywords):
                    return
            elif record.levelno < logging.WARNING:
                return

            symbol = None
            m = re.search(r"\b[A-Z]{3,6}USDT\b", msg)
            if m:
                symbol = m.group(0)
            if not symbol:
                configured_asset = os.getenv("ATIVOS", "").split(",")[0].strip().upper()
                if configured_asset:
                    symbol = configured_asset if configured_asset.endswith("USDT") else f"{configured_asset}USDT"

            strategy = None
            for name in (
                "BREAKOUT",
                "MEAN_REVERSION",
                "EARLY_TREND",
                "MARKET_MAKING_LATERAL",
                "MARKET_MAKING",
                "PULLBACK_TREND_FOLLOWING",
                "SWING_TREND_FOLLOWING",
                "MR",
                "MM",
            ):
                if name.lower() in msg_lower:
                    strategy = name
                    break

            ts = datetime.utcfromtimestamp(record.created).isoformat()
            with self._lock:
                placeholder = "%s" if database_backend() == "postgres" else "?"
                self._conn.execute(
                    f"INSERT INTO log_events (ts, level, logger, strategy, symbol, message) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
                    (ts, record.levelname, record.name, strategy, symbol, msg),
                )
                self._conn.commit()
        except Exception:
            self.handleError(record)


def configurar_logger_global(nome_arquivo: str = "trading_bot_geneses.log", modo: str = "info"):
    logger = logging.getLogger()
    # Módulos de estratégia são importados pelo worker e alguns ainda
    # mantêm chamadas legadas a esta função. Depois que o worker configura
    # o logger principal, essas chamadas não podem substituir seus handlers.
    if getattr(logger, "_genesis_configured", False):
        return logger
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    configured_path = os.getenv("GENESIS_LOG_FILE", "").strip()
    caminho_log = Path(configured_path) if configured_path else Path(__file__).resolve().parents[1] / "logs" / nome_arquivo
    caminho_log.parent.mkdir(parents=True, exist_ok=True)

    # Fix: info mode must not force DEBUG.
    nivel = logging.DEBUG if modo == "debug" else logging.INFO
    formatter = SafeUTF8Formatter("%(asctime)s - %(levelname)s - %(message)s")

    stream_handler = logging.StreamHandler(_configure_utf8_stream(sys.__stdout__))
    stream_handler.setFormatter(formatter)

    file_handler = TimedRotatingFileHandler(
        str(caminho_log), when="midnight", interval=1, backupCount=30,
        encoding="utf-8", delay=False,
    )
    file_handler.setFormatter(formatter)

    logger.setLevel(nivel)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger._genesis_configured = True

    db_enabled = os.getenv("LOG_DB_ENABLED", "1").strip().lower() in ("1", "true", "yes", "y")
    if db_enabled:
        db_path = os.getenv("LOG_DB_PATH", "trades.db")
        db_keywords = os.getenv(
            "LOG_DB_KEYWORDS",
            "autorizado,reprovado,entrada,ordem,stop_limit,fill,filled,cancel,timeout,erro,trend gate,openai,regime,score",
        )
        db_handler = OperationalDatabaseHandler(db_path, keywords=db_keywords)
        db_handler.setFormatter(formatter)
        logger.addHandler(db_handler)
