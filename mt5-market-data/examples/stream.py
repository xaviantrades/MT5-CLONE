"""Example: print live quotes while candles are continuously stored."""
import asyncio
import logging
import os

from mt5_market_data import MarketDataClient

logging.basicConfig(level=logging.INFO)


async def main():
    client = MarketDataClient(
        login=int(os.environ["MT5_LOGIN"]),
        password=os.environ["MT5_PASSWORD"],
        server=os.environ["MT5_SERVER"],
        symbols=os.getenv("MT5_SYMBOLS", "EURUSDm").split(","),
        timeframes=os.getenv("MT5_TIMEFRAMES", "M1,M5,H1").split(","),
        database=os.getenv("MT5_DATABASE", "market_data.sqlite3"),
        initial_bars=2000,
        backfill_interval=15,
    )

    client.on_quote = lambda q: print(
        f"TICK {q.symbol} bid={q.bid} ask={q.ask} time={q.timestamp.isoformat()}"
    )
    client.on_candle = lambda c: print(
        f"BAR {c.symbol} {c.timeframe} {c.timestamp} "
        f"O={c.open} H={c.high} L={c.low} C={c.close}"
    )

    try:
        await client.run_forever()
    finally:
        client.close_store()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
