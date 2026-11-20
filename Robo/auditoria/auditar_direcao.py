import argparse
import csv
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_ts(value):
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def to_float(value):
    try:
        v = float(value)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return None


def load_trades(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r["_dt"] = parse_ts(r.get("timestamp"))
    rows = [r for r in rows if r["_dt"] is not None]
    rows.sort(key=lambda x: x["_dt"])
    return rows


def classify_from_fields(row):
    reg = str(row.get("regime") or "").lower()
    ten = str(row.get("tendencia") or "").lower()
    txt = f"{reg} {ten}"
    if any(k in txt for k in ("bear", "baixa", "down", "downtrend")):
        return "BEAR", "fields"
    if any(k in txt for k in ("bull", "alta", "up", "uptrend")):
        return "BULL", "fields"
    if any(k in txt for k in ("lateral", "range", "consolid", "neutra", "neutro")):
        return "LATERAL", "fields"
    return None, None


def load_market(symbol: str, root: Path, cache: dict):
    key = symbol.upper()
    if key in cache:
        return cache[key]
    path = root / f"market_data_{key}_5m.csv"
    data = []
    if path.exists():
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            rd = csv.DictReader(f)
            fields = rd.fieldnames or []
            map_lower = {c.lower(): c for c in fields}
            tcol = map_lower.get("time") or map_lower.get("timestamp") or map_lower.get("datetime") or map_lower.get("date")
            ccol = map_lower.get("close")
            if tcol and ccol:
                for row in rd:
                    ts = parse_ts(row.get(tcol))
                    cl = to_float(row.get(ccol))
                    if ts is not None and cl is not None:
                        data.append((ts, cl))
    data.sort(key=lambda x: x[0])
    cache[key] = data
    return data


def classify_rebuild(row, root: Path, cache: dict):
    symbol = (row.get("symbol") or "").upper()
    data = load_market(symbol, root, cache)
    if not data:
        return "UNKNOWN", "unknown_no_market_data"
    ts = row["_dt"]
    idx = None
    lo, hi = 0, len(data) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if data[mid][0] <= ts:
            idx = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if idx is None or idx < 220:
        return "UNKNOWN", "unknown_insufficient_history"
    closes = [x[1] for x in data[max(0, idx - 260):idx + 1]]
    if len(closes) < 205:
        return "UNKNOWN", "unknown_insufficient_history"

    def sma(seq, n):
        return sum(seq[-n:]) / n if len(seq) >= n else None

    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    sma50_prev = sma(closes[:-5], 50) if len(closes) > 55 else None
    if sma50 is None or sma200 is None or sma50_prev is None:
        return "UNKNOWN", "unknown_sma_missing"
    slope = sma50 - sma50_prev

    if sma50 < sma200 and slope < 0:
        return "BEAR", "rebuild"
    if sma50 > sma200 and slope > 0:
        return "BULL", "rebuild"
    return "LATERAL", "rebuild"


def metrics_by_side(rows, side: str):
    side_rows = [r for r in rows if str(r.get("side") or "").lower() == side]
    pnl = [to_float(r.get("profit_loss")) for r in side_rows]
    pnl = [x for x in pnl if x is not None]
    wins = sum(1 for x in pnl if x > 0)
    losses = sum(1 for x in pnl if x < 0)
    gp = sum(x for x in pnl if x > 0)
    gl = abs(sum(x for x in pnl if x < 0))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    wr = (wins / (wins + losses) * 100.0) if (wins + losses) else 0.0
    return {
        "count": len(side_rows),
        "pnl_count": len(pnl),
        "wins": wins,
        "losses": losses,
        "winrate": wr,
        "pf": pf,
        "net": sum(pnl),
    }


def build_fallback_flags(row):
    flags = []
    side = str(row.get("side") or "").strip().lower()
    strategy = str(row.get("strategy") or "").strip().lower()
    decision = str(row.get("decision_source") or "").strip().lower()
    entry_score = to_float(row.get("entry_score"))
    regime = row.get("regime")
    tendencia = row.get("tendencia")

    if side == "":
        flags.append("side_missing_db")
    if side not in ("buy", "sell"):
        flags.append("side_invalid_db")
    if side == "buy" and decision == "monitoramento" and strategy in ("bybit_existing", "continuation_trade"):
        flags.append("fallback_risk_buy_monitoramento")
    if side == "buy" and entry_score in (None, 0.0):
        flags.append("buy_without_score_signal")
    if regime in (None, "") and tendencia in (None, ""):
        flags.append("no_regime_fields")
    return flags


def print_window_report(name, rows):
    buy = metrics_by_side(rows, "buy")
    sell = metrics_by_side(rows, "sell")
    buy_pf = "inf" if math.isinf(buy["pf"]) else f"{buy['pf']:.4f}"
    sell_pf = "inf" if math.isinf(sell["pf"]) else f"{sell['pf']:.4f}"
    print(f"\n[{name}] trades={len(rows)}")
    print(
        f"BUY count={buy['count']} pnl_n={buy['pnl_count']} wins={buy['wins']} losses={buy['losses']} "
        f"winrate={buy['winrate']:.2f}% pf={buy_pf} net={buy['net']:.8f}"
    )
    print(
        f"SELL count={sell['count']} pnl_n={sell['pnl_count']} wins={sell['wins']} losses={sell['losses']} "
        f"winrate={sell['winrate']:.2f}% pf={sell_pf} net={sell['net']:.8f}"
    )

    by_strategy = defaultdict(lambda: {"count": 0, "net": 0.0})
    by_decision = defaultdict(lambda: {"count": 0, "net": 0.0})
    for r in rows:
        pnl = to_float(r.get("profit_loss")) or 0.0
        s = r.get("strategy") or "N/A"
        d = r.get("decision_source") or "N/A"
        by_strategy[s]["count"] += 1
        by_strategy[s]["net"] += pnl
        by_decision[d]["count"] += 1
        by_decision[d]["net"] += pnl

    print("Por strategy:")
    for k, v in sorted(by_strategy.items(), key=lambda kv: kv[1]["count"], reverse=True):
        print(f"- {k}: trades={v['count']} net={v['net']:.8f}")
    print("Por decision_source:")
    for k, v in sorted(by_decision.items(), key=lambda kv: kv[1]["count"], reverse=True):
        print(f"- {k}: trades={v['count']} net={v['net']:.8f}")

    counts = defaultdict(int)
    for r in rows:
        counts[r["_regime_class"]] += 1
    print(
        f"Regime: BULL={counts['BULL']} BEAR={counts['BEAR']} "
        f"LATERAL={counts['LATERAL']} UNKNOWN={counts['UNKNOWN']}"
    )

    bear = [r for r in rows if r["_regime_class"] == "BEAR"]
    bear_buy = [r for r in bear if str(r.get("side") or "").lower() == "buy"]
    bear_sell = [r for r in bear if str(r.get("side") or "").lower() == "sell"]
    bear_buy_net = sum((to_float(r.get("profit_loss")) or 0.0) for r in bear_buy)
    bear_sell_net = sum((to_float(r.get("profit_loss")) or 0.0) for r in bear_sell)
    pct_buy = (100.0 * len(bear_buy) / len(bear)) if bear else 0.0
    pct_sell = (100.0 * len(bear_sell) / len(bear)) if bear else 0.0
    print(
        f"BEAR crítico: BUY_em_BEAR={len(bear_buy)} ({pct_buy:.2f}%) net={bear_buy_net:.8f} | "
        f"SELL_em_BEAR={len(bear_sell)} ({pct_sell:.2f}%) net={bear_sell_net:.8f}"
    )
    if len(bear_sell) == 0:
        print("SELL em BEAR não ocorreu na janela (possível bloqueio excessivo ou ausência de sinal SELL).")


def main():
    parser = argparse.ArgumentParser(description="Auditoria de direção BUY/SELL do Robô Fluxo")
    parser.add_argument("--db", default="trades.db", help="Caminho do SQLite trades.db")
    parser.add_argument("--root", default=".", help="Raiz do projeto (market_data_*.csv)")
    parser.add_argument("--sample", type=int, default=20, help="Amostra de trades para mapa de origem")
    parser.add_argument("--out-csv", default="", help="CSV opcional de saída (ex.: auditoria/out/mapa.csv)")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    root = Path(args.root).resolve()
    rows = load_trades(db_path)
    now = datetime.now(timezone.utc)

    market_cache = {}
    for r in rows:
        cls, src = classify_from_fields(r)
        if cls is None:
            cls, src = classify_rebuild(r, root, market_cache)
        r["_regime_class"] = cls
        r["_regime_source"] = src
        flags = build_fallback_flags(r)
        r["_fallback_flags"] = ",".join(flags) if flags else ""

    print("=== AUDITORIA DIRECIONAL BUY/SELL ===")
    print(f"DB: {db_path}")
    print(f"Agora UTC: {now.isoformat()}")
    print(f"Trades totais: {len(rows)}")

    windows = {
        "24h": now - timedelta(hours=24),
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
    }
    for name, cutoff in windows.items():
        w = [r for r in rows if cutoff <= r["_dt"] <= now]
        print_window_report(name, w)

    print("\nMapa de origem do side (amostra mais recente):")
    sample_rows = sorted(rows, key=lambda x: x["_dt"], reverse=True)[: max(1, args.sample)]
    print("timestamp | symbol | side | strategy | decision_source | regime_class | regime_source | fallback_flags")
    for r in sample_rows:
        print(
            f"{r.get('timestamp')} | {r.get('symbol')} | {r.get('side')} | {r.get('strategy')} | "
            f"{r.get('decision_source')} | {r['_regime_class']} | {r['_regime_source']} | {r['_fallback_flags']}"
        )

    if args.out_csv:
        out_path = Path(args.out_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "timestamp",
            "trade_id",
            "symbol",
            "side",
            "strategy",
            "decision_source",
            "profit_loss",
            "_regime_class",
            "_regime_source",
            "_fallback_flags",
        ]
        with out_path.open("w", encoding="utf-8", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=fields)
            wr.writeheader()
            for r in rows:
                wr.writerow({k: r.get(k) for k in fields})
        print(f"\nCSV salvo: {out_path}")


if __name__ == "__main__":
    main()

