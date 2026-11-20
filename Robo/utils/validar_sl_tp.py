import logging
from typing import Tuple, Optional, List

log = logging.getLogger(__name__)

def validar_sl_tp(
    entry_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    side: str,
    tolerance: float = 1e-4,
    auto_fix: bool = False,
    raise_on_error: bool = True,
) -> Tuple[float, float]:
    """
    Valida (e opcionalmente corrige) SL/TP.

    Retorna: (stop_loss, take_profit) já arredondados.
    Lança ValueError se `raise_on_error=True` e encontrar erro.
    """
    side = side.lower()
    errs: List[str] = []

    # Normaliza para 2 casas
    if stop_loss is not None:
        stop_loss = round(float(stop_loss), 2)
    if take_profit is not None:
        take_profit = round(float(take_profit), 2)

    if side == "buy":
        if stop_loss is not None and stop_loss >= entry_price - tolerance:
            errs.append(
                f"STOP-LOSS (BUY) deve ser < entry_price — SL={stop_loss}, entry={entry_price}"
            )
            if auto_fix:
                stop_loss = round(entry_price * 0.995, 2)

        if take_profit is not None and take_profit <= entry_price + tolerance:
            errs.append(
                f"TAKE-PROFIT (BUY) deve ser > entry_price — TP={take_profit}, entry={entry_price}"
            )
            if auto_fix:
                take_profit = round(entry_price * 1.005, 2)

    elif side == "sell":
        if stop_loss is not None and stop_loss <= entry_price + tolerance:
            errs.append(
                f"STOP-LOSS (SELL) deve ser > entry_price — SL={stop_loss}, entry={entry_price}"
            )
            if auto_fix:
                stop_loss = round(entry_price * 1.005, 2)

        if take_profit is not None and take_profit >= entry_price - tolerance:
            errs.append(
                f"TAKE-PROFIT (SELL) deve ser < entry_price — TP={take_profit}, entry={entry_price}"
            )
            if auto_fix:
                take_profit = round(entry_price * 0.995, 2)
    else:
        errs.append(f"Side inválido: {side}")

    # Se existem erros e não estamos em modo auto-fix
    if errs and not auto_fix:
        msg = " | ".join(errs)
        log.error("⛔ " + msg)
        if raise_on_error:
            raise ValueError(msg)
    elif errs and auto_fix:
        log.warning(f"⚠️ Valores corrigidos automaticamente: {errs}")

    log.info(f"SL/TP validados – side={side.upper()}, entry={entry_price}, SL={stop_loss}, TP={take_profit}")
    return stop_loss, take_profit
