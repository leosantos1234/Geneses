"""
Teste manual rápido para o fluxo de fail-safe de ATR no monitoramento.

Cenário A: ATR presente (recovery OK) -> trailing/BE liberados.
Cenário B: ATR ausente (recovery FAIL) -> trailing/BE bloqueados.
"""

import math


def avaliar_atr(indicators: dict):
    allow_trailing_raw = indicators.get("ALLOW_TRAILING", 1)
    allow_trailing = str(allow_trailing_raw).strip().lower() not in ("0", "false", "no")

    atr_val = None
    atr_raw = indicators.get("ATR")
    if atr_raw is not None:
        try:
            parsed = float(atr_raw)
            if parsed > 0 and not math.isnan(parsed):
                atr_val = parsed
        except Exception:
            atr_val = None

    trailing_be_liberado = (atr_val is not None) and allow_trailing
    return atr_val, trailing_be_liberado


def calcular_offset(atr_valor: float, entry_price: float, stop_loss: float):
    trailing_atr_monitor_mult = 1.8
    trailing_min_entry_pct = 0.014
    trailing_absolute_min = 100.0

    bruto = max(
        atr_valor * (trailing_atr_monitor_mult * 1.3),
        trailing_min_entry_pct * entry_price,
        atr_valor,
        trailing_absolute_min,
    )
    max_offset = abs(entry_price - stop_loss)
    return min(bruto, max_offset)


def main():
    entry_price = 200.0
    stop_loss = 180.0

    print("=== Caso A: recovery OK (ATR real) ===")
    indicators_ok = {
        "ATR": 4.2,
        "RSI": 51.0,
        "SMA_10": 199.0,
        "SMA_50": 195.0,
        "strategy": "bybit_existing_recovered",
        "timeframe": "5",
    }
    atr_ok, liberado_ok = avaliar_atr(indicators_ok)
    print(f"ATR={atr_ok} | trailing_be_liberado={liberado_ok}")
    if liberado_ok:
        offset = calcular_offset(atr_ok, entry_price, stop_loss)
        print(f"offset_calculado={offset:.4f}")

    print("\n=== Caso B: recovery FAIL (sem ATR) ===")
    indicators_fail = {
        "strategy": "bybit_existing_no_context",
        "ALLOW_TRAILING": 0,
        "timeframe": "5",
    }
    atr_fail, liberado_fail = avaliar_atr(indicators_fail)
    print(f"ATR={atr_fail} | trailing_be_liberado={liberado_fail}")
    if not liberado_fail:
        print("FAIL-SAFE ativo: trailing/BE bloqueados.")


if __name__ == "__main__":
    main()
