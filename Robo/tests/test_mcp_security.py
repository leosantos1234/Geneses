"""Checks estáticos da fronteira read-only do MCP V1."""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "Robo" / "mcp" / "server.py"
sys.path.insert(0, str(ROOT))


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    forbidden_text = ("sqlite3", "trades.db", "sqlite://", "place_order", "create_order", "cancel_order", "close_position", "set_trading_stop", "executar_ordem", "enviar_ordem", "subprocess", "os.system")
    assert not any(token in text for token in forbidden_text), [token for token in forbidden_text if token in text]
    tree = ast.parse(text)
    forbidden_modules = {"trading_bot_geneses", "compra1", "exchange", "execution_gate", "tomar_decisao_comandante"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported.intersection(forbidden_modules), imported.intersection(forbidden_modules)
    os.environ.setdefault("GENESIS_DATABASE_BACKEND", "postgres")
    os.environ.setdefault("GENESIS_DATABASE_URL", "postgresql://redacted@127.0.0.1:5432/genesis_trading")
    import Robo.mcp.server as server
    with patch.object(server, "database_health", return_value={"backend": "postgres", "status": "unavailable", "canonical": True, "target": "127.0.0.1:5432/genesis_trading"}):
        health = server.get_system_health()
        assert health.status == "degraded"
        assert "redacted" not in health.model_dump_json()
    with patch.object(server, "database_backend", return_value="sqlite"), patch.object(server, "database_health", return_value={"backend": "sqlite", "status": "healthy", "canonical": False}):
        assert server.list_trades(limit=1).error.code == "DATABASE_UNAVAILABLE"
        assert server.get_risk_status().error.code == "DATABASE_UNAVAILABLE"
    assert server.list_trades(limit=0).error.code == "INVALID_ARGUMENT"
    assert server.list_trades(start_time="not-a-date").error.code == "INVALID_ARGUMENT"
    assert server.list_open_positions(symbol="DROP;TABLE").error.code == "INVALID_ARGUMENT"
    assert server.get_trade_detail("").error.code == "INVALID_ARGUMENT"
    print({"forbidden_imports": False, "forbidden_write_access": False, "invalid_arguments": True, "health_unavailable": True})


if __name__ == "__main__":
    main()
