from mt5_market_data.models import Candle
from mt5_market_data.storage import CandleStore


def test_store_upserts_and_orders(tmp_path):
    store = CandleStore(str(tmp_path / "history.sqlite3"))
    try:
        first = Candle("EURUSDm", "M1", 60, 1, 2, 0.5, 1.5, 10, 1)
        updated = Candle("EURUSDm", "M1", 60, 1, 2.1, 0.5, 2, 11, 1)
        second = Candle("EURUSDm", "M1", 120, 2, 3, 1, 2.5, 12, 1)
        store.upsert([first, updated, second])
        rows = store.get_candles("EURUSDm", "M1", limit=10)
        assert len(rows) == 2
        assert rows[0].high == 2.1
        assert rows[0].close == 2
        assert store.latest_timestamp("EURUSDm", "M1") == 120
    finally:
        store.close()
