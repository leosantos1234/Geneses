"""Pullback Trend Following multi-timeframe."""

from .strategy import (
    STRATEGY_NAME,
    avaliar_setup_entrada_1h,
    avaliar_setup_entrada_4h,
    backtest_entrada_1h,
)

__all__ = ["STRATEGY_NAME", "avaliar_setup_entrada_1h", "avaliar_setup_entrada_4h", "backtest_entrada_1h"]
