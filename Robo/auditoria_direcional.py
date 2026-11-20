import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple


LOG_NAME = "trading_bot_geneses.log"
DEFAULT_OUTPUT = "auditoria_direcional.json"
MAX_ENTRIES = 20


TIMESTAMP_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+-\s+(?P<level>[A-Z]+)\s+-\s+(?P<msg>.*)$"
)

REGIME_PATTERNS = [
    re.compile(r"regime de mercado detectado:\s*([a-z0-9_ \-]+)", re.I),
    re.compile(r"regime detectado para [^:]+:\s*([a-z0-9_ \-]+)", re.I),
    re.compile(r"regime\s*=\s*([a-z0-9_ \-]+)", re.I),
]

TENDENCIA_PATTERNS = [
    re.compile(
        r"tend[eê]ncia(?:_15m| 15m|_macro| macro)?[^a-z0-9]*(buy|sell|neutral|neutra|alta|baixa|up|down|long|short)",
        re.I,
    ),
]

CONSENSO_PATTERNS = [
    re.compile(r"direc[ãa]o\s+sugerida:\s*([a-z]+)", re.I),
    re.compile(r"direc[ãa]o\s*:\s*([a-z]+)", re.I),
    re.compile(r"direc[ãa]o\s*=\s*([a-z]+)", re.I),
]

LSTM_PATTERNS = [
    re.compile(r"lstm sugere:\s*([a-z]+)", re.I),
    re.compile(r"lstm .*lado=([a-z]+)", re.I),
]

ORDER_DIR_PATTERNS = [
    re.compile(r"\bside\s*=\s*(buy|sell)\b", re.I),
    re.compile(r"\bordem\b.*\b(compra|venda|buy|sell)\b", re.I),
    re.compile(r"\bexecutar_(compra|venda)\b", re.I),
]

ENTRY_MARKERS = [
    "ordem enviada",
    "ordem enviada com sucesso",
    "ordem limit enviada",
    "ordem limit enviada",
    "ordem criada",
    "entrada autorizada",
    "entrada realizada",
    "entrada confirmada",
    "ordem executada",
    "ordem preenchida",
    "order_id",
    "orderid",
]


def normalize_dir(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip().lower()
    if v in {"buy", "compra", "long", "alta", "up"}:
        return "buy"
    if v in {"sell", "venda", "short", "baixa", "down"}:
        return "sell"
    if v in {"neutral", "neutra", "neutro", "hold"}:
        return "neutral"
    return v


def find_latest_log() -> Optional[str]:
    logs_dir = "logs"
    candidates: List[str] = []
    if os.path.isdir(logs_dir):
        for name in os.listdir(logs_dir):
            if name.endswith(f"_{LOG_NAME}"):
                candidates.append(os.path.join(logs_dir, name))
    if candidates:
        return max(candidates, key=lambda p: os.path.getmtime(p))
    if os.path.isfile(LOG_NAME):
        return LOG_NAME
    return None


def read_lines(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []
    except Exception:
        try:
            with open(path, "r", encoding="latin-1", errors="replace") as f:
                return f.read().splitlines()
        except Exception:
            return []


def extract_first(patterns: List[re.Pattern], text: str) -> Optional[str]:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def is_entry_line(text: str) -> bool:
    lower = text.lower()
    if any(marker in lower for marker in ENTRY_MARKERS):
        return True
    if "ordem" in lower and ("buy" in lower or "sell" in lower or "compra" in lower or "venda" in lower):
        return True
    if "side=" in lower:
        return True
    return False


def parse_log(path: str) -> Tuple[List[Dict], Dict]:
    lines = read_lines(path)
    entries: List[Dict] = []

    current_regime: Optional[str] = None
    current_tendencia: Optional[str] = None
    current_consenso: Optional[str] = None
    current_lstm: Optional[str] = None
    current_order_dir: Optional[str] = None

    for line in lines:
        m = TIMESTAMP_RE.match(line)
        if not m:
            continue

        ts = m.group("ts")
        msg = m.group("msg")

        regime = extract_first(REGIME_PATTERNS, msg)
        if regime:
            current_regime = regime.strip().lower()

        tendencia = extract_first(TENDENCIA_PATTERNS, msg)
        if tendencia:
            current_tendencia = normalize_dir(tendencia)

        consenso = extract_first(CONSENSO_PATTERNS, msg)
        if consenso:
            current_consenso = normalize_dir(consenso)

        lstm = extract_first(LSTM_PATTERNS, msg)
        if lstm:
            current_lstm = normalize_dir(lstm)

        order_dir = extract_first(ORDER_DIR_PATTERNS, msg)
        if order_dir:
            current_order_dir = normalize_dir(order_dir)

        if is_entry_line(msg):
            entry = {
                "timestamp": ts,
                "regime": current_regime,
                "tendencia": current_tendencia,
                "consenso": current_consenso,
                "lstm": current_lstm,
                "direcao_ordem": current_order_dir,
            }
            entries.append(entry)

    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    summary = {
        "log_path": path,
        "total_trades": len(entries),
    }
    return entries, summary


def compute_desalinhamento(tendencia: Optional[str], consenso: Optional[str], direcao_ordem: Optional[str]) -> bool:
    if tendencia == "sell" and consenso == "buy":
        return True
    if consenso and direcao_ordem and consenso != direcao_ordem:
        return True
    return False


def print_table(entries: List[Dict]) -> None:
    headers = ["timestamp", "regime", "tendencia", "consenso", "direcao_ordem", "desalinhamento"]
    rows = []
    for e in entries:
        desalinhado = compute_desalinhamento(e.get("tendencia"), e.get("consenso"), e.get("direcao_ordem"))
        row = [
            e.get("timestamp") or "",
            e.get("regime") or "",
            e.get("tendencia") or "",
            e.get("consenso") or "",
            e.get("direcao_ordem") or "",
            str(desalinhado),
        ]
        rows.append(row)

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def fmt_row(values: List[str]) -> str:
        return " | ".join(val.ljust(col_widths[i]) for i, val in enumerate(values))

    print(fmt_row(headers))
    print("-+-".join("-" * w for w in col_widths))
    for row in rows:
        print(fmt_row(row))


def main() -> int:
    log_path = find_latest_log()
    if not log_path:
        print("Nenhum arquivo de log encontrado para auditoria.")
        return 1

    entries, summary = parse_log(log_path)

    # calcula desalinhamento e agrega
    desalinhamentos = 0
    for e in entries:
        e["desalinhamento"] = compute_desalinhamento(
            e.get("tendencia"), e.get("consenso"), e.get("direcao_ordem")
        )
        if e["desalinhamento"]:
            desalinhamentos += 1

    total = len(entries)
    pct = (desalinhamentos / total * 100.0) if total else 0.0

    print_table(entries)
    print()
    print(f"Total de trades analisados: {total}")
    print(f"Desalinhamentos encontrados: {desalinhamentos}")
    print(f"Percentual de desalinhamento: {pct:.2f}%")

    output = {
        "log_path": summary["log_path"],
        "total_trades": total,
        "desalinhamentos": desalinhamentos,
        "percentual_desalinhamento": pct,
        "entries": entries,
    }
    with open(DEFAULT_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
