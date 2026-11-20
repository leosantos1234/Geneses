from genesis_rag.rag_v1 import normalize_symbol


def test_supported_symbol_forms_normalize_to_genesis_symbol():
    for value in ("SOL/USD", "SOL-USDT", "SOL-USDT-SWAP", "SOLUSDT"):
        assert normalize_symbol(value) == "SOLUSDT"


def test_normalization_is_generic_for_supported_assets():
    assert normalize_symbol("BTC/USD") == "BTCUSDT"
    assert normalize_symbol("ETH-USDT-SWAP") == "ETHUSDT"
