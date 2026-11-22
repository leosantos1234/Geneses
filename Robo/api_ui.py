from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env.ui"), override=False)

from ui_backend import repository
from ui_backend.auth import authenticate, create_user, issue_token, require_auth
from ui_backend import worker_manager
from genesis_runtime import database_health


app = FastAPI(
    title="Genesis UI API",
    description="API estável para o frontend (Lovable/UI).",
    version="1.0.0",
)

cors_origins_raw = os.getenv(
    "UI_CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:8080,http://127.0.0.1:3000,http://127.0.0.1:5173,http://127.0.0.1:8080",
)
cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_BALANCE_CACHE: dict[str, Any] = {"expires_at": 0.0, "data": None}
_BALANCE_LOCK = threading.Lock()

class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreateRequest(BaseModel):
    email: str
    password: str
    role: str = "viewer"


class WorkerSelectionRequest(BaseModel):
    assets: list[str]
    strategy: str

@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    try:
        user = authenticate(payload.email, payload.password)
        return {"user": user, "access_token": issue_token(user), "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Serviço de autenticação indisponível: {type(exc).__name__}") from exc

@app.get("/api/auth/me")
def current_user(user: dict[str, str] = Depends(require_auth)) -> dict[str, Any]:
    return {"user": user}


def _require_admin(user: dict[str, str] = Depends(require_auth)) -> dict[str, str]:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem gerenciar usuários")
    return user


@app.get("/api/auth/users")
def users(_admin: dict[str, str] = Depends(_require_admin)) -> list[dict[str, Any]]:
    repository.ensure_ui_users_table()
    return repository.list_ui_users()


@app.post("/api/auth/users", status_code=201)
def add_user(payload: UserCreateRequest, _admin: dict[str, str] = Depends(_require_admin)) -> dict[str, Any]:
    try:
        return {"user": create_user(payload.email, payload.password, payload.role)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("aberto", "open"):
        return "open"
    if raw in ("fechado", "closed"):
        return "closed"
    if raw in ("cancelado", "cancelled"):
        return "cancelled"
    return raw or "closed"


def _normalize_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["status"] = _normalize_status(out.get("status"))
    side = str(out.get("side") or "").lower()
    out["side"] = "buy" if side == "buy" else ("sell" if side == "sell" else side)
    if out.get("trend") is None and out.get("tendencia") is not None:
        out["trend"] = out.get("tendencia")
    return out


@app.get("/api/ui/health")
def health() -> dict[str, Any]:
    db = database_health()
    return {
        "status": "ok" if db.get("status") == "healthy" and db.get("canonical") else "degraded",
        "service": "genesis-ui-api",
        "database": db,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/ui/summary")
def summary(
    days: int = Query(default=7, ge=1, le=365),
    symbol: str | None = Query(default=None),
    _user: dict[str, str] = Depends(require_auth),
) -> dict[str, Any]:
    data = repository.get_summary(days=days, symbol=symbol)
    recent = repository.list_trades(symbol=symbol, status="closed", limit=5, offset=0)
    recent_n = [_normalize_trade_row(r) for r in recent]
    profits = [float(r.get("profit_loss") or 0.0) for r in recent_n if (r.get("profit_loss") is not None)]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    return {
        **data,
        "avg_profit": float(sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": float(sum(losses) / len(losses)) if losses else 0.0,
        "best_trade": float(max(profits)) if profits else 0.0,
        "worst_trade": float(min(profits)) if profits else 0.0,
        "recent_trades": recent_n,
    }


@app.get("/api/ui/performance-breakdown")
def performance_breakdown(
    days: int = Query(default=7, ge=1, le=3650),
    _user: dict[str, str] = Depends(require_auth),
) -> dict[str, Any]:
    return repository.get_performance_breakdown(days=days)


@app.get("/api/ui/open-positions")
def open_positions(
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _user: dict[str, str] = Depends(require_auth),
) -> list[dict[str, Any]]:
    rows = repository.list_open_positions(symbol=symbol, limit=limit)
    return [_normalize_trade_row(r) for r in rows]


def _exchange_balance_snapshot(days: int = 7) -> dict[str, Any]:
    now = time.monotonic()
    cache_key = f"data_{days}"
    with _BALANCE_LOCK:
        cached = _BALANCE_CACHE.get(cache_key)
        if cached and now < float(_BALANCE_CACHE.get("expires_at", 0.0)):
            return cached
    from exchange.factory import active_exchange_name, create_exchange_client

    client = create_exchange_client()
    wallet = client.get_wallet_balance(accountType="UNIFIED")
    accounts = wallet.get("result", {}).get("list", [])
    account = accounts[0] if accounts else {}
    total_equity = float(account.get("totalEquity") or 0.0)
    available_account = float(account.get("totalAvailableBalance") or 0.0)
    usdt = next((coin for coin in account.get("coin", []) if str(coin.get("coin", "")).upper() == "USDT"), {})
    usdt_equity = float(usdt.get("equity") or usdt.get("walletBalance") or 0.0)
    available_usdt = float(usdt.get("totalAvailableBalance") or usdt.get("availableToWithdraw") or 0.0)
    funding_usdt = 0.0
    funding_available = 0.0
    if hasattr(client, "get_funding_balance"):
        funding_response = client.get_funding_balance(coin="USDT")
        funding_coin = next((coin for coin in funding_response.get("result", {}).get("list", []) if str(coin.get("coin", "")).upper() == "USDT"), {})
        funding_usdt = float(funding_coin.get("equity") or funding_coin.get("walletBalance") or 0.0)
        funding_available = float(funding_coin.get("totalAvailableBalance") or funding_coin.get("availableToWithdraw") or 0.0)

    positions_response = client.get_positions(category="linear")
    positions = []
    notional = 0.0
    unrealized_pnl = 0.0
    for position in positions_response.get("result", {}).get("list", []):
        try:
            size = abs(float(position.get("pos") or position.get("size") or 0.0))
            position_notional = abs(float(position.get("notionalUsd") or position.get("notional") or 0.0))
            pnl = float(position.get("upl") or position.get("unrealizedPnl") or 0.0)
        except (TypeError, ValueError):
            continue
        if size <= 0 and position_notional <= 0:
            continue
        notional += position_notional
        unrealized_pnl += pnl
        positions.append({
            "symbol": position.get("symbol") or position.get("instId"),
            "side": position.get("side"),
            "size": size,
            "entry_price": float(position.get("avgPrice") or position.get("avgEntryPrice") or position.get("entryPrice") or 0.0),
            "current_price": float(position.get("markPrice") or position.get("markPx") or position.get("lastPrice") or 0.0),
            "leverage": float(position.get("leverage") or position.get("lever") or 0.0),
            "notional_usd": position_notional,
            "unrealized_pnl": pnl,
            "unrealized_pnl_pct": float(position.get("uplRatio") or 0.0) * 100.0,
        })

    history = repository.get_summary(days=days)
    snapshot = {
        "exchange": active_exchange_name().upper(),
        "currency": "USDT",
        "total_equity": total_equity + funding_usdt,
        "trading_equity": usdt_equity,
        "funding_balance": funding_usdt,
        "available_usdt": (available_usdt or available_account) + funding_available,
        "used_in_positions": max(usdt_equity - (available_usdt or available_account), 0.0),
        "positions_notional": notional,
        "unrealized_pnl": unrealized_pnl,
        "period_days": days,
        "realized_pnl": float(history.get("pnl_total") or 0.0),
        "period_trades": int(history.get("total_trades") or 0),
        "open_positions": positions,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
    with _BALANCE_LOCK:
        _BALANCE_CACHE.update({cache_key: snapshot, "expires_at": time.monotonic() + 15.0})
    return snapshot


@app.get("/api/ui/exchange-balance")
def exchange_balance(
    days: int = Query(default=7, ge=1, le=3650),
    _user: dict[str, str] = Depends(_require_admin),
) -> dict[str, Any]:
    try:
        return _exchange_balance_snapshot(days=days)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Saldo da corretora indisponível: {type(exc).__name__}") from exc


@app.get("/api/ui/trades")
def trades(
    symbol: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(open|closed)?$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _user: dict[str, str] = Depends(require_auth),
) -> dict[str, Any]:
    rows = repository.list_trades(
        symbol=symbol,
        status=status,
        limit=limit,
        offset=offset,
    )
    total = repository.count_trades(symbol=symbol, status=status)
    return {
        "data": [_normalize_trade_row(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/ui/trades/{trade_id}")
def trade_detail(trade_id: str, _user: dict[str, str] = Depends(require_auth)) -> dict[str, Any]:
    row = repository.get_trade(trade_id)
    if not row:
        raise HTTPException(status_code=404, detail="trade_not_found")
    return _normalize_trade_row(row)


@app.get("/api/ui/filters")
def filters(_user: dict[str, str] = Depends(require_auth)) -> dict[str, list[str]]:
    return repository.get_filters()


@app.get("/api/ui/control-center")
def control_center(
    days: int = Query(default=30, ge=1, le=90),
    _user: dict[str, str] = Depends(require_auth),
) -> dict[str, Any]:
    return repository.get_control_center(days=days)


@app.get("/api/ui/runtime-options")
def runtime_options(_user: dict[str, str] = Depends(require_auth)) -> dict[str, Any]:
    options = worker_manager.options()
    if os.getenv("GENESIS_REMOTE_WORKER_CONTROL", "").strip().lower() in {"1", "true", "yes", "on"}:
        repository.ensure_ui_worker_states_table()
        options["workers"] = repository.list_ui_worker_states()
    return options


@app.get("/api/ui/workers")
def workers(_user: dict[str, str] = Depends(require_auth)) -> list[dict[str, Any]]:
    return worker_manager.status()


@app.get("/api/ui/logs")
def logs(
    asset: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    hours: float | None = Query(default=None, gt=0, le=720),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    _user: dict[str, str] = Depends(require_auth),
) -> list[dict[str, Any]]:
    return repository.list_ui_logs(asset=asset, limit=limit, hours=hours, since=since, until=until)


@app.get("/api/ui/logs/page")
def logs_page(
    asset: str | None = Query(default=None),
    limit: int = Query(default=250, ge=1, le=1000),
    hours: float | None = Query(default=None, gt=0, le=720),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    before_id: int | None = Query(default=None, ge=1),
    level: str | None = Query(default=None),
    category: str | None = Query(default=None),
    _user: dict[str, str] = Depends(require_auth),
) -> dict[str, Any]:
    return repository.list_ui_logs_page(
        asset=asset,
        limit=limit,
        hours=hours,
        since=since,
        until=until,
        before_id=before_id,
        level=level,
        category=category,
    )


@app.get("/api/ui/logs/download", response_class=PlainTextResponse)
def download_logs(
    asset: str | None = Query(default=None),
    limit: int = Query(default=100000, ge=1, le=100000),
    hours: float | None = Query(default=None, gt=0, le=720),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    level: str | None = Query(default=None),
    category: str | None = Query(default=None),
    _user: dict[str, str] = Depends(require_auth),
) -> PlainTextResponse:
    rows: list[dict[str, Any]] = []
    before_id: int | None = None
    page_size = min(limit, 1000)
    while len(rows) < limit:
        page = repository.list_ui_logs_page(
            asset=asset,
            limit=min(page_size, limit - len(rows)),
            hours=hours,
            since=since,
            until=until,
            before_id=before_id,
            level=level,
            category=category,
        )
        batch = page["items"]
        if not batch:
            break
        rows.extend(batch)
        if not page["has_more"]:
            break
        before_id = page["next_before_id"]
    lines = [
        f"[{row.get('ts', '')}] [{row.get('level', '')}] "
        f"[{row.get('symbol') or 'UNKNOWN'}] "
        f"[{row.get('strategy') or 'UNKNOWN'}] {row.get('message', '')}"
        for row in reversed(rows)
    ]
    selected = (asset or "todos").strip().upper().replace("/", "-")
    return PlainTextResponse(
        "\n".join(lines) + ("\n" if lines else ""),
        headers={"Content-Disposition": f'attachment; filename="genesis-logs-{selected}.log"'},
    )


@app.post("/api/ui/workers/start")
def start_workers(payload: WorkerSelectionRequest, _user: dict[str, str] = Depends(_require_admin)) -> dict[str, Any]:
    if not payload.assets:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um ativo")
    try:
        if os.getenv("GENESIS_REMOTE_WORKER_CONTROL", "").strip().lower() in {"1", "true", "yes", "on"}:
            allowed = {item["id"] for item in worker_manager.available_assets()}
            invalid = [asset for asset in payload.assets if asset.upper() not in allowed]
            if invalid:
                raise ValueError(f"Ativo(s) indisponível(is): {', '.join(invalid)}")
            return {"workers": repository.set_ui_worker_states([asset.upper() for asset in payload.assets], running=True, strategy=payload.strategy)}
        return {"workers": worker_manager.start(payload.assets, payload.strategy)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ui/workers/stop")
def stop_workers(payload: WorkerSelectionRequest, _user: dict[str, str] = Depends(_require_admin)) -> dict[str, Any]:
    if not payload.assets:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um ativo")
    try:
        if os.getenv("GENESIS_REMOTE_WORKER_CONTROL", "").strip().lower() in {"1", "true", "yes", "on"}:
            return {"workers": repository.set_ui_worker_states([asset.upper() for asset in payload.assets], running=False)}
        return {"workers": worker_manager.stop(payload.assets)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("UI_API_HOST", "0.0.0.0")
    port = int(os.getenv("UI_API_PORT", "8010"))
    uvicorn.run("api_ui:app", host=host, port=port, reload=False)
