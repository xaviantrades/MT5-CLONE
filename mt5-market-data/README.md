# MT5 Market Data Client

A focused Python client extracted from the MT5-CLONE WebSocket protocol research. It does only two jobs:

1. Maintain an authenticated MT5 WebSocket session and publish live quotes for requested symbols.
2. Continuously backfill and refresh historical OHLC candles, storing them in SQLite.

It intentionally contains **no** order placement, trade execution, account balance, position, or pending-order API.

## How it works

1. Resolve the broker server name to its WebSocket access endpoints using the MetaQuotes SearchMQ service, with the Search endpoint as fallback.
2. Open `wss://HOST:PORT/terminal`.
3. Send command 0 encrypted with the protocol's static AES-256-CBC key. Extract the 32-byte session key from the authentication response.
4. Send command 28 with the researched 912-byte login payload.
5. Start one receive loop and a heartbeat loop. Heartbeat command 51 is sent every three seconds.
6. Request the symbol catalogue with command 34, map broker symbol names to their internal IDs, then subscribe using command 7.
7. Decode server-pushed command 8 messages (50-byte quote records) and invoke `on_quote`.
8. Request rates with command 11. The payload is 74 bytes: UTF-16LE symbol field, timeframe ID, start Unix time and end Unix time. Decode 48-byte candle records.
9. Upsert candles into SQLite using `(symbol, timeframe, timestamp)` as the primary key.
10. Repeat history synchronization. Each pass starts one candle before the last stored timestamp so the current/incomplete bar can be refreshed. Large ranges are split into bounded pages.
11. If the socket or heartbeat fails, reconnect with exponential backoff, authenticate again, resubscribe, and resume history from the last stored candle.

## Install

Python 3.10+ is required.

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# Linux/macOS:
source .venv/bin/activate
pip install -e .
```

## Run the example

Set credentials as environment variables; do not hardcode them in source files.

PowerShell:
```powershell
$env:MT5_LOGIN="12345678"
$env:MT5_PASSWORD="your-password"
$env:MT5_SERVER="Broker-MT5Live"
$env:MT5_SYMBOLS="EURUSDm,GBPUSDm"
$env:MT5_TIMEFRAMES="M1,M5,H1"
python examples/stream.py
```

Linux/macOS:
```bash
export MT5_LOGIN=12345678
export MT5_PASSWORD='your-password'
export MT5_SERVER='Broker-MT5Live'
export MT5_SYMBOLS='EURUSDm,GBPUSDm'
export MT5_TIMEFRAMES='M1,M5,H1'
python examples/stream.py
```

The SQLite database is created at `market_data.sqlite3` by default. Quotes are delivered to the callback; candles are delivered to `on_candle` and persisted.

## Use as a library

```python
import asyncio
from mt5_market_data import MarketDataClient

async def main():
    feed = MarketDataClient(
        login=12345678,
        password="read-from-environment",
        server="Broker-MT5Live",
        symbols=["EURUSDm", "GBPUSDm"],
        timeframes=["M1", "M5", "H1"],
        database="prices.sqlite3",
        initial_bars=2000,
        backfill_interval=15,
    )

    feed.on_quote = lambda q: print(q.symbol, q.bid, q.ask)
    feed.on_candle = lambda c: print(c.symbol, c.timeframe, c.timestamp, c.close)

    try:
        await feed.run_forever()
    finally:
        feed.close_store()

asyncio.run(main())
```

Read locally cached candles with `feed.get_history("EURUSDm", "M1", limit=500)`.

## Important behavior and limitations

- The live stream is quote/tick data (bid, ask, last, tick volume, timestamp and flags). It is not a guaranteed lossless exchange tick archive.
- Historical backfill is broker-provided OHLC candle history. The engine refreshes the latest overlapping candle and catches up from the last persisted timestamp after disconnects.
- A candle is keyed by its broker timestamp. Repeated history responses update the existing row instead of creating duplicates.
- Symbol names are broker-specific. Use the exact catalogue name (for example, a broker may use `EURUSDm`, not `EURUSD`).
- History availability and maximum range are controlled by the broker/server. This client cannot create history the broker does not provide.
- The researched WebSocket certificate is issued for a hostname while broker discovery returns IP addresses. For compatibility, `verify_tls=False` is the default, matching the source research. This disables certificate identity verification and carries a man-in-the-middle risk. Set `verify_tls=True` where the endpoint certificate validates correctly; a production deployment should use a verified hostname/certificate path.
- This implements a reverse-engineered, undocumented protocol. Broker updates may change behavior. Test with a demo account before relying on it.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

Tests cover packet layout, quote/candle parsing, rates request layout and SQLite deduplication. They do not replace live broker integration tests.
