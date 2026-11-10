from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class Symbol:
    name: str
    description: str
    digits: int
    symbol_id: int
    path: str = ""


@dataclass(slots=True)
class Quote:
    symbol: str
    symbol_id: int
    bid: float
    ask: float
    last: float
    tick_volume: int
    timestamp: datetime
    flags: int = 0


@dataclass(slots=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
