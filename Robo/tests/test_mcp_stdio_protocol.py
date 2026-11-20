"""Harness síncrono JSON-RPC STDIO para validar o protocolo MCP real no Windows."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"get_system_health", "list_open_positions", "list_trades", "get_trade_detail", "get_risk_status"}


def snapshot() -> tuple:
    with psycopg.connect(os.environ["GENESIS_DATABASE_URL"], options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
        return tuple(conn.execute("SELECT COUNT(*) FROM trades").fetchone()) + tuple(conn.execute("SELECT COUNT(*) FROM predictions_lstm").fetchone()) + tuple(conn.execute("SELECT COUNT(*) FROM log_events").fetchone()) + tuple(conn.execute("SELECT COUNT(*) FROM mr_fsm_state").fetchone())


class Client:
    def __init__(self):
        env = os.environ.copy(); env.pop("PYTHONPATH", None)
        self.proc = subprocess.Popen([sys.executable, "-m", "Robo.mcp.server"], cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.seq = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        request_id = self.seq
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(payload) + "\n"); self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(self.proc.stderr.read())
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    def notify(self, method: str, params: dict | None = None) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n"); self.proc.stdin.flush()

    def close(self):
        self.proc.terminate(); self.proc.wait(timeout=5)


def main() -> None:
    before = snapshot(); client = Client()
    try:
        initialized = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "genesis-sprint3-test", "version": "1"}})
        assert "result" in initialized, initialized
        client.notify("notifications/initialized")
        listed = client.request("tools/list")
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names == EXPECTED, names
        health = client.request("tools/call", {"name": "get_system_health", "arguments": {}})
        positions = client.request("tools/call", {"name": "list_open_positions", "arguments": {"limit": 2}})
        positions_filtered = client.request("tools/call", {"name": "list_open_positions", "arguments": {"symbol": "BNBUSDT", "limit": 1}})
        trades = client.request("tools/call", {"name": "list_trades", "arguments": {"limit": 2}})
        trades_page = client.request("tools/call", {"name": "list_trades", "arguments": {"limit": 2, "offset": 2}})
        content = trades["result"].get("structuredContent") or {}
        trade_id = content["trades"][0]["trade_id"]
        detail = client.request("tools/call", {"name": "get_trade_detail", "arguments": {"trade_id": trade_id}})
        missing = client.request("tools/call", {"name": "get_trade_detail", "arguments": {"trade_id": "missing-trade-id"}})
        risk = client.request("tools/call", {"name": "get_risk_status", "arguments": {}})
        invalid = client.request("tools/call", {"name": "list_trades", "arguments": {"limit": 0}})
        for response in (health, positions, positions_filtered, trades, trades_page, detail, risk):
            assert "result" in response and not response["result"].get("isError"), response
        assert (missing["result"].get("structuredContent") or {}).get("error", {}).get("code") == "NOT_FOUND"
        invalid_payload = invalid.get("result", {}).get("structuredContent", {})
        assert invalid.get("result", {}).get("isError") or invalid_payload.get("error", {}).get("code") == "INVALID_ARGUMENT"
        print({"protocol": "stdio-jsonrpc", "initialized": True, "discovered": sorted(names), "all_five_invoked": True, "invalid_argument_checked": True})
    finally:
        client.close()
    after = snapshot(); assert before == after, (before, after)
    print({"snapshot_before": before, "snapshot_after": after, "database_unchanged": True})


if __name__ == "__main__":
    main()
