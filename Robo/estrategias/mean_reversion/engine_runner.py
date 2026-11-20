from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from .mean_reversion_engine import MeanReversionEngine
from .mean_reversion_fsm import FSMConfig, MRState, MarketSnapshot, MeanReversionStateMachine, PositionSnapshot
from .mr_executor import BybitMRExecutor
from .mr_state_store import MRStateStore

logger = logging.getLogger(__name__)

# Cache global: 1 engine por símbolo
_MR_ENGINES: dict[str, MeanReversionEngine] = {}


def _mr_position_snapshot_bybit(session: Any, symbol: str) -> PositionSnapshot:
    """Lê posição atual na Bybit e converte para PositionSnapshot (somente leitura)."""
    try:
        resp = session.get_positions(category="linear", symbol=symbol)
        positions = (resp or {}).get("result", {}).get("list", []) or []
        if not positions:
            return PositionSnapshot(in_position=False)

        p = positions[0]
        size = float(p.get("size", 0) or 0)
        if size <= 0:
            return PositionSnapshot(in_position=False)

        side_raw = (p.get("side") or "").strip().lower()  # "Buy"/"Sell"
        side = "buy" if side_raw == "buy" else ("sell" if side_raw == "sell" else None)

        entry_price_raw = p.get("avgPrice") or p.get("entryPrice") or p.get("entry_price")
        entry_price = float(entry_price_raw) if entry_price_raw not in (None, "", "0") else None

        return PositionSnapshot(in_position=True, side=side, entry_price=entry_price, size=size, raw=p)
    except Exception:
        return PositionSnapshot(in_position=False)


def _get_engine(session: Any, *, symbol: str, qty: float) -> MeanReversionEngine:
    engine = _MR_ENGINES.get(symbol)
    if engine is not None:
        return engine

    cfg = FSMConfig(
        confirm_fill_via_exchange=True,
        require_mr_regime=False,
    )
    fsm = MeanReversionStateMachine(cfg=cfg)
    executor = BybitMRExecutor(session=session, category="linear")
    store = MRStateStore(os.getenv("MR_DB_PATH", "trades.db"))

    engine = MeanReversionEngine(fsm=fsm, executor=executor, store=store, qty=float(qty))
    _MR_ENGINES[symbol] = engine
    return engine


def executar_mean_reversion_nova_tick(
    *,
    session: Any,
    symbol: str,
    df: Any,
    interval: str,
    qty: float,
    regime_override: int | None = None,
) -> MRState:
    """Executa 1 tick da MR nova (stateful) e retorna apenas o estado atual."""
    engine = _get_engine(session, symbol=symbol, qty=float(qty))

    market = MarketSnapshot(
        symbol=symbol,
        timeframe=str(interval),
        now=datetime.now(timezone.utc),
        df=df,
        indicators={"regime": (regime_override if regime_override is not None else "lateral")},
    )
    position = _mr_position_snapshot_bybit(session, symbol)
    engine.on_tick(market, position)
    return engine.fsm.state
