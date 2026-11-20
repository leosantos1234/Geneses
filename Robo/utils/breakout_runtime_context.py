"""Canonical BreakoutEvaluation runtime argument composition."""


def resolve_breakout_regime_override(*, canonical_regime, natural_runtime_context, configured_override=None):
    """Use the canonical regime only for a complete production context."""
    production_context = (
        isinstance(natural_runtime_context, dict)
        and natural_runtime_context.get("record_provenance") == "PRODUCTION_RUNTIME"
        and natural_runtime_context.get("evaluation_context") == "PRODUCTION_RUNTIME"
    )
    if production_context and canonical_regime in (1, 2, 3, 4):
        return int(canonical_regime)
    return configured_override


def select_backtest_regime_override(*, regime, natural_runtime_context, dynamic_regime_enabled):
    """Select the regime override without weakening production authority."""
    production_context = (
        isinstance(natural_runtime_context, dict)
        and natural_runtime_context.get("record_provenance") == "PRODUCTION_RUNTIME"
        and natural_runtime_context.get("evaluation_context") == "PRODUCTION_RUNTIME"
    )
    if production_context:
        return regime
    return None if dynamic_regime_enabled else regime


def regime_authority_metadata(*, natural_runtime_context, regime_recalculated):
    """Return JSON-safe observability metadata for regime authority."""
    production_context = (
        isinstance(natural_runtime_context, dict)
        and natural_runtime_context.get("record_provenance") == "PRODUCTION_RUNTIME"
        and natural_runtime_context.get("evaluation_context") == "PRODUCTION_RUNTIME"
    )
    return {
        "regime_source": "CANONICAL_RUNTIME" if production_context else "DYNAMIC_BACKTEST",
        "regime_recalculated": bool(regime_recalculated) if not production_context else False,
    }


def build_breakout_evaluation_kwargs(
    *,
    symbol,
    timeframe,
    regime,
    record_provenance,
    evaluation_context,
    run_id,
    breakout_evaluation_id,
    persist,
    evaluation_candle,
    natural_context=False,
    breakout_lineage=None,
):
    """Compose BreakoutEvaluation arguments with one source per context field."""
    if natural_context:
        # Production context is the canonical source for runtime identity fields.
        kwargs = dict(evaluation_candle)
        for metadata_key in ("canonical_regime", "regime_source", "regime_recalculated"):
            kwargs.pop(metadata_key, None)
        kwargs.setdefault("symbol", symbol)
        kwargs.setdefault("timeframe", timeframe)
        kwargs.setdefault("regime", regime)
    else:
        kwargs = {
            "symbol": symbol,
            "timeframe": timeframe,
            "regime": regime,
            "record_provenance": record_provenance,
            "evaluation_context": evaluation_context,
            "run_id": run_id,
        }
        kwargs.update(evaluation_candle)
    kwargs["breakout_evaluation_id"] = breakout_evaluation_id
    kwargs["persist"] = persist
    if breakout_lineage:
        kwargs.update(dict(breakout_lineage))
    return kwargs
