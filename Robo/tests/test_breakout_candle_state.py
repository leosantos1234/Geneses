from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from utils.breakout_candle_state import (
    canonical_breakout_symbol,
    candle_state_path,
    get_last_candle,
    set_last_candle,
)


class BreakoutCandleStateTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = Path(self._cwd) / "Robo" / "tests" / f".breakout_state_test_{uuid4().hex}"
        self._tmp.mkdir(parents=True)
        os.chdir(self._tmp)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_symbols_are_isolated(self):
        symbols = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
        for symbol in symbols:
            set_last_candle("2026-10-10 12:00:00", symbol)
        for symbol in symbols:
            self.assertEqual(get_last_candle(symbol), "2026-10-10 12:00:00")
        self.assertEqual(len({str(candle_state_path(s)) for s in symbols}), 5)

    def test_same_timestamp_is_allowed_for_each_symbol(self):
        timestamp = "2026-10-10 12:05:00"
        for symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"):
            self.assertIsNone(get_last_candle(symbol))
            set_last_candle(timestamp, symbol)
            self.assertEqual(get_last_candle(symbol), timestamp)

    def test_same_symbol_same_timestamp_is_detectable_as_duplicate(self):
        set_last_candle("2026-10-10 12:10:00", "BTCUSDT")
        self.assertEqual(get_last_candle("BTCUSDT"), "2026-10-10 12:10:00")
        self.assertIsNone(get_last_candle("ETHUSDT"))

    def test_legacy_shared_file_does_not_seed_symbols(self):
        Path("ultimo_candle_breakout.txt").write_text("2026-10-10 12:15:00", encoding="utf-8")
        self.assertIsNone(get_last_candle("BTCUSDT"))
        self.assertIsNone(get_last_candle("ETHUSDT"))

    def test_symbols_are_sanitized(self):
        self.assertEqual(canonical_breakout_symbol("btc-usdt.swap"), "BTCUSDTSWAP")
        self.assertEqual(candle_state_path("BTC-USDT"), "ultimo_candle_breakout_BTCUSDT.txt")


if __name__ == "__main__":
    unittest.main()
