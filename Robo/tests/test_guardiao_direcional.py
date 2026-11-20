import unittest

from auditoria.guardiao_direcional import permitir_operacao


class TestGuardiaoDirecional(unittest.TestCase):
    def test_bear_buy_breakout_veto(self):
        ok, motivo, fator = permitir_operacao("buy", "BEAR", "breakout")
        self.assertFalse(ok)
        self.assertIn("bloqueado_buy_em_bear", motivo)
        self.assertEqual(fator, 0.0)

    def test_bear_sell_breakout_ok(self):
        ok, motivo, fator = permitir_operacao("sell", "BEAR", "breakout")
        self.assertTrue(ok)
        self.assertEqual(motivo, "alinhado_com_regime_macro")
        self.assertEqual(fator, 1.0)

    def test_bull_sell_breakout_veto(self):
        ok, motivo, fator = permitir_operacao("sell", "BULL", "breakout")
        self.assertFalse(ok)
        self.assertIn("bloqueado_sell_em_bull", motivo)
        self.assertEqual(fator, 0.0)

    def test_side_missing_veto(self):
        ok, motivo, fator = permitir_operacao(None, "BEAR", "breakout")
        self.assertFalse(ok)
        self.assertEqual(motivo, "side_ausente_ou_invalido")
        self.assertEqual(fator, 0.0)


if __name__ == "__main__":
    unittest.main()

