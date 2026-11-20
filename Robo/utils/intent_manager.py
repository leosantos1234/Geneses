import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StrategyIntent:
    symbol: str
    strategy: str
    created_ts: float
    expires_ts: float
    reason: str = ""

    def is_active(self, now_ts: Optional[float] = None) -> bool:
        now = float(now_ts if now_ts is not None else time.time())
        return now < float(self.expires_ts)


class IntentManager:
    """Gerencia 'intenções' por símbolo (edge aprovado vs timing de execução)."""

    def __init__(self) -> None:
        self._intents: dict[str, StrategyIntent] = {}

    def get_active(self, symbol: str, now_ts: Optional[float] = None) -> Optional[StrategyIntent]:
        key = (symbol or "").upper()
        intent = self._intents.get(key)
        if not intent:
            return None
        if not intent.is_active(now_ts=now_ts):
            self._intents.pop(key, None)
            return None
        return intent

    def upsert(self, *, symbol: str, strategy: str, ttl_seconds: float, reason: str = "") -> StrategyIntent:
        now = time.time()
        key = (symbol or "").upper()
        intent = StrategyIntent(
            symbol=key,
            strategy=(strategy or "").replace(" ", "").upper(),
            created_ts=now,
            expires_ts=now + max(1.0, float(ttl_seconds)),
            reason=reason,
        )
        self._intents[key] = intent
        logger.info(
            f"[INTENT] Criada/atualizada | symbol={key} | strategy={intent.strategy} | ttl_s={int(ttl_seconds)} | reason={reason}"
        )
        return intent

    def clear(self, symbol: str, *, reason: str = "") -> None:
        key = (symbol or "").upper()
        if key in self._intents:
            self._intents.pop(key, None)
            logger.info(f"[INTENT] Removida | symbol={key} | reason={reason}")

    def has_active(self, symbol: str) -> bool:
        return self.get_active(symbol) is not None

