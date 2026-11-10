"""Small durable SQLite candle store; timestamps are UTC Unix seconds."""
import sqlite3
from pathlib import Path

from .models import Candle


class CandleStore:
    def __init__(self, path: str = "market_data.sqlite3"):
        self.path = str(Path(path))
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                tick_volume INTEGER NOT NULL,
                spread INTEGER NOT NULL,
                PRIMARY KEY (symbol, timeframe, timestamp)
            )
        """)
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_candles_lookup
            ON candles(symbol, timeframe, timestamp)
        """)
        self.db.commit()

    def latest_timestamp(self, symbol: str, timeframe: str) -> int | None:
        row = self.db.execute(
            "SELECT MAX(timestamp) FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def upsert(self, candles: list[Candle]) -> int:
        if not candles:
            return 0
        before = self.db.total_changes
        self.db.executemany("""
            INSERT INTO candles
            (symbol,timeframe,timestamp,open,high,low,close,tick_volume,spread)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,timeframe,timestamp) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, tick_volume=excluded.tick_volume,
                spread=excluded.spread
        """, [
            (c.symbol, c.timeframe, c.timestamp, c.open, c.high, c.low,
             c.close, c.tick_volume, c.spread)
            for c in candles
        ])
        self.db.commit()
        return self.db.total_changes - before

    def get_candles(
        self, symbol: str, timeframe: str, limit: int = 500,
        start: int | None = None, end: int | None = None,
    ) -> list[Candle]:
        sql = """
            SELECT timestamp,open,high,low,close,tick_volume,spread
            FROM candles WHERE symbol=? AND timeframe=?
        """
        params: list = [symbol, timeframe]
        if start is not None:
            sql += " AND timestamp>=?"
            params.append(start)
        if end is not None:
            sql += " AND timestamp<=?"
            params.append(end)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self.db.execute(sql, params).fetchall()
        rows.reverse()
        return [
            Candle(symbol, timeframe, *row)
            for row in rows
        ]

    def close(self):
        self.db.close()
