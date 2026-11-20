"""Render dispatcher for the panel's remote asset controls."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from ui_backend import repository
from banco_trade.db_trades import inicializar_banco

ROOT = Path(__file__).resolve().parent
children: dict[str, subprocess.Popen] = {}


def _assets() -> list[str]:
    return [item.strip().upper() for item in os.getenv("GENESIS_ASSET_LIST", "BTC,ETH,BNB,SOL,XRP,AVAX").split(",") if item.strip()]


def _stop(asset: str) -> None:
    process = children.pop(asset, None)
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _start(asset: str, strategy: str) -> None:
    if asset in children and children[asset].poll() is None:
        return
    env = os.environ.copy()
    env["ATIVOS"] = f"{asset}USDT"
    env["GENESIS_STRATEGY_SELECTION"] = strategy
    env["ROBO_ENV_FILE"] = ""
    children[asset] = subprocess.Popen(
        [sys.executable, str(ROOT / "trading_bot_geneses.py")],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    print(f"worker iniciado asset={asset} strategy={strategy}", flush=True)


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
                print(f"worker encerrado asset={asset} code={process.returncode}", flush=True)
                children.pop(asset, None)
        time.sleep(5)


if __name__ == "__main__":
    main()
