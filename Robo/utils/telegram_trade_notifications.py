"""Notificacoes Telegram do Swing; falhas aqui nunca bloqueiam o robô."""

from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timezone

import requests

from .utils_telegram import _get_telegram_config, _env_flag

logger = logging.getLogger(__name__)


def _api_url(method: str) -> str | None:
    token, _ = _get_telegram_config()
    return f"https://api.telegram.org/bot{token}/{method}" if token else None


def _panel_url(path: str) -> str:
    base = os.getenv("GENESIS_PANEL_URL", "https://genesis-panel.onrender.com").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _brl_rate() -> float | None:
    configured = os.getenv("USDT_BRL_RATE", "").strip()
    if configured:
        try:
            return float(configured)
        except ValueError:
            pass
    try:
        response = requests.get("https://api.frankfurter.app/latest?from=USD&to=BRL", timeout=5)
        response.raise_for_status()
        return float(response.json()["rates"]["BRL"])
    except Exception as exc:
        logger.warning("[TELEGRAM] cotacao USD/BRL indisponivel: %s", type(exc).__name__)
        return None


def _send_photo(image: bytes, caption: str) -> None:
    if not _env_flag("TELEGRAM_ENABLED", True):
        return
    url = _api_url("sendPhoto")
    _, chat_id = _get_telegram_config()
    if not url or not chat_id:
        logger.warning("[TELEGRAM] chat nao configurado para enviar foto")
        return
    try:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": ("swing_trade.png", image, "image/png")},
            timeout=8,
        )
        if response.status_code != 200:
            logger.warning("[TELEGRAM] foto rejeitada: status=%s", response.status_code)
    except Exception as exc:
        logger.warning("[TELEGRAM] falha ao enviar foto: %s", type(exc).__name__)


def notify_swing_entry(symbol: str, side: str, entry: float, stop: float, target: float,
                       trailing: float, quantity: float, candles=None, order_id: str | None = None) -> None:
    """Envia o desenho da entrada sem impedir a execução em caso de falha."""
    try:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 5), dpi=130)
        if candles is not None and not getattr(candles, "empty", True):
            frame = candles.tail(80).copy()
            ax.plot(frame["time"], frame["close"], color="#38bdf8", linewidth=1.6, label="Fechamento")
            ax.tick_params(axis="x", rotation=20)
        ax.axhline(entry, color="#22c55e", label=f"Entrada {entry:.6f}")
        ax.axhline(stop, color="#ef4444", linestyle="--", label=f"Stop {stop:.6f}")
        ax.axhline(target, color="#a3e635", linestyle="--", label=f"Alvo {target:.6f}")
        ax.axhline(entry - trailing if side.lower() == "buy" else entry + trailing,
                    color="#f59e0b", linestyle=":", label="Trailing")
        ax.set_title(f"Genesis Quant | {symbol} | Swing {side.upper()}")
        ax.grid(alpha=0.2)
        ax.legend(loc="best", fontsize=8)
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        caption = (f"📈 ENTRADA SWING\nAtivo: {symbol}\nLado: {side.upper()}\n"
                   f"Entrada: {entry:.6f}\nQuantidade: {quantity}\nStop: {stop:.6f}\n"
                   f"Alvo: {target:.6f}\nTrailing: {trailing:.6f}\n"
                   f"Ordem: {order_id or 'n/d'}\n"
                   f"Ver posição: {_panel_url('positions')}")
        _send_photo(buffer.getvalue(), caption)
    except Exception as exc:
        logger.warning("[TELEGRAM] falha ao montar grafico de entrada: %s", type(exc).__name__)


def notify_swing_exit(symbol: str, side: str, entry: float, exit_price: float,
                      quantity: float, reason: str, order_id: str | None = None) -> None:
    """Envia o encerramento com P/L em USDT e BRL quando a cotacao estiver disponivel."""
    try:
        pnl_usdt = (exit_price - entry) * quantity if side.lower() == "buy" else (entry - exit_price) * quantity
        rate = _brl_rate()
        pnl_brl = pnl_usdt * rate if rate is not None else None
        status = "POSITIVO" if pnl_usdt >= 0 else "NEGATIVO"
        message = (f"🏁 FIM DO TRADE SWING — {status}\nAtivo: {symbol}\nLado: {side.upper()}\n"
                   f"Entrada: {entry:.6f}\nSaída: {exit_price:.6f}\nQuantidade: {quantity}\n"
                   f"Resultado: {pnl_usdt:+.4f} USDT")
        message += f"\nResultado em BRL: {pnl_brl:+.2f}" if pnl_brl is not None else "\nResultado em BRL: cotação indisponível"
        message += f"\nMotivo: {reason}\nOrdem: {order_id or 'n/d'}"
        if order_id:
            message += f"\nVer detalhe: {_panel_url(f'trades/{order_id}')}"
        from .utils_telegram import send_telegram_message
        send_telegram_message(message)
    except Exception as exc:
        logger.warning("[TELEGRAM] falha ao notificar encerramento: %s", type(exc).__name__)
