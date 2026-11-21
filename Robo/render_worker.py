"""Render dispatcher for the panel's remote asset controls."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from ui_backend import repository
from banco_trade.db_trades import inicializar_banco

ROOT = Path(__file__).resolve().parent
children: dict[str, subprocess.Popen] = {}
child_started_at: dict[str, float] = {}
restart_counts: dict[str, int] = {}


def _assets() -> list[str]:
    return [item.strip().upper() for item in os.getenv("GENESIS_ASSET_LIST", "BTC,ETH,BNB,SOL,XRP,AVAX").split(",") if item.strip()]


def _stop(asset: str) -> None:
    process = children.pop(asset, None)
    child_started_at.pop(asset, None)
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _start(asset: str, strategy: str) -> None:
    existing = children.get(asset)
    if existing is not None:
        if existing.poll() is None:
            return
        # O filho pode ter terminado entre duas iterações. Registre-o antes
        # de substituí-lo, senão o motivo da queda fica perdido.
        _log_child_exit(asset, existing)
        children.pop(asset, None)
    env = os.environ.copy()
    env["ATIVOS"] = f"{asset}USDT"
    env["GENESIS_STRATEGY_SELECTION"] = strategy
    env["ROBO_ENV_FILE"] = ""
    # Garante que cada print do processo filho apareça imediatamente no Render.
    env["PYTHONUNBUFFERED"] = "1"
    started_at = time.monotonic()
    children[asset] = subprocess.Popen(
        [sys.executable, str(ROOT / "trading_bot_geneses.py")],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    threading.Thread(target=_forward_child_output, args=(asset, strategy, children[asset]), daemon=True).start()
    child_started_at[asset] = started_at
    restart_counts[asset] = restart_counts.get(asset, 0) + 1
    print(
        f"worker iniciado asset={asset} strategy={strategy} "
        f"pid={children[asset].pid} tentativa={restart_counts[asset]}",
        flush=True,
    )


def _forward_child_output(asset: str, strategy: str, process: subprocess.Popen) -> None:
    """Mirror child stdout/stderr to Render and the Dashboard log store."""
    stream = process.stdout
    if stream is None:
        return
    try:
        for line in stream:
            message = line.rstrip("\r\n")
            if not message:
                continue
            print(f"[{asset}] {message}", flush=True)
            level = "ERROR" if any(token in message.upper() for token in ("ERROR", "TRACEBACK", "EXCEPTION")) else "WARNING" if "WARNING" in message.upper() else "INFO"
            try:
                repository.append_ui_log(asset=asset, strategy=strategy, message=message, level=level)
            except Exception as exc:
                print(f"[LOG_PERSISTENCE] asset={asset} error={type(exc).__name__}", flush=True)
    finally:
        stream.close()


def _log_child_exit(asset: str, process: subprocess.Popen) -> None:
    returncode = process.returncode
    uptime = time.monotonic() - child_started_at.pop(asset, time.monotonic())
    if returncode is None:
        detail = "returncode=desconhecido"
    elif returncode < 0:
        detail = f"returncode={returncode} sinal={-returncode}"
    else:
        detail = f"returncode={returncode}"
    print(
        f"worker encerrado asset={asset} pid={process.pid} {detail} "
        f"uptime_seconds={uptime:.1f}",
        flush=True,
    )


def _shutdown(_signum, _frame) -> None:
    for asset in list(children):
        _stop(asset)
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    inicializar_banco()
    print("Schema operacional PostgreSQL verificado antes dos ativos", flush=True)
    repository.ensure_ui_worker_states_table()
    print(f"Render worker dispatcher ativo; assets={','.join(_assets())}", flush=True)
    while True:
        desired = {row["asset"]: row for row in repository.list_ui_worker_states()}
        for asset in _assets():
            state = desired.get(asset, {})
            if state.get("running"):
                _start(asset, str(state.get("strategy") or "SWING_TREND_FOLLOWING"))
            else:
                _stop(asset)
        for asset, process in list(children.items()):
            if process.poll() is not None:
                _log_child_exit(asset, process)
                children.pop(asset, None)
        time.sleep(5)


if __name__ == "__main__":
    main()
