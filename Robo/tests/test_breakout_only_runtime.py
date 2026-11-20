from __future__ import annotations

import unittest

from regime.entry_profile import runtime_strategy_for_regime


class BreakoutOnlyRuntimeTests(unittest.TestCase):
    def test_breakout_only_regime_1_keeps_breakout_eligible(self):
        self.assertEqual(runtime_strategy_for_regime(1, "1"), "BREAKOUT")

    def test_breakout_only_regime_2_keeps_breakout_eligible(self):
        self.assertEqual(runtime_strategy_for_regime(2, "true"), "BREAKOUT")

    def test_breakout_only_regime_3_does_not_select_market_making(self):
        self.assertIsNone(runtime_strategy_for_regime(3, "1"))

    def test_breakout_only_regime_4_has_no_strategy(self):
        self.assertIsNone(runtime_strategy_for_regime(4, "1"))

    def test_disabled_breakout_only_preserves_market_making_regime_3(self):
        self.assertEqual(runtime_strategy_for_regime(3, "0"), "MARKET_MAKING_LATERAL")


if __name__ == "__main__":
    unittest.main()
