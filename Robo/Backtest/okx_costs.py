"""Consulta e normaliza custos específicos da conta OKX para backtests."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any


def _rows(response: dict) -> list[dict]:
    return list((response or {}).get("result", {}).get("list", []) or [])


def normalize_fee_response(response: dict) -> dict[str, Any]:
    """Extrai maker/taker da resposta normalizada do cliente OKX."""
    rows = _rows(response)
    if not rows:
        return {"maker": None, "taker": None, "level": None, "raw": response}
    row = rows[0]
    group = (row.get("feeGroup") or [{}])[0]
    maker = group.get("maker", row.get("maker"))
    taker = group.get("taker", row.get("taker"))
    return {
        # A OKX retorna taxas como débitos negativos; o backtest usa magnitude
        # positiva para subtrair o custo do PnL.
        "maker": abs(float(maker)) if maker not in (None, "") else None,
        "taker": abs(float(taker)) if taker not in (None, "") else None,
        "level": row.get("level"),
        "instType": row.get("instType"),
        "raw": response,
    }


def get_account_costs(client, symbol: str) -> dict[str, Any]:
    """Consulta taxa efetiva da conta e funding atual sem exibir credenciais."""
    fees = normalize_fee_response(client.get_trade_fee(category="linear", symbol=symbol))
    funding_rows = _rows(client.get_funding_rate(category="linear", symbol=symbol))
    funding = funding_rows[0] if funding_rows else {}
    return {
        "symbol": symbol,
        "maker": fees.get("maker"),
        "taker": fees.get("taker"),
        "fee_level": fees.get("level"),
        "funding_rate": float(funding["fundingRate"]) if funding.get("fundingRate") not in (None, "") else None,
        "funding_time": funding.get("fundingTime"),
        "next_funding_time": funding.get("nextFundingTime"),
    }


def export_funding_history(client, symbol: str, output_path: str, limit: int = 100) -> str:
    """Exporta funding histórico para o CSV aceito pelo backtest.

    O CSV gerado tem as colunas ``time`` e ``funding_rate``. O endpoint limita
    a consulta; chamadas adicionais podem ser feitas posteriormente para
    ampliar o período.
    """
    response = client.get_funding_rate_history(category="linear", symbol=symbol, limit=limit)
    rows = _rows(response)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time", "funding_rate"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "time": row.get("fundingTime") or row.get("ts"),
                "funding_rate": row.get("fundingRate", 0),
            })
    return str(path)
