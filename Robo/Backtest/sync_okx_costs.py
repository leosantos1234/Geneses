"""Sincroniza taxas da conta e funding histórico da OKX para o backtest.

Uso, a partir de Robo:
    python -m Backtest.sync_okx_costs --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from exchange.factory import create_exchange_client
from Backtest.okx_costs import export_funding_history, get_account_costs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--out-dir", default="dados_historicos/funding")
    args = parser.parse_args()

    load_dotenv(".env-okx", override=False)
    client = create_exchange_client()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for symbol in args.symbols:
        costs = get_account_costs(client, symbol)
        print(json.dumps(costs, ensure_ascii=False))
        output = out_dir / f"{symbol.upper()}_funding.csv"
        export_funding_history(client, symbol, str(output), limit=100)
        print(f"funding_csv={output}")


if __name__ == "__main__":
    main()

