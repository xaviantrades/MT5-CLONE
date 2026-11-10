"""Market-data-only MT5 client with reconnect and continuous candle synchronization."""
import asyncio
import inspect
import logging
import struct
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from .broker import find_server_endpoints
from .connection import MT5Connection
from .models import Candle, Quote, Symbol
from .protocol import (
    TIMEFRAME_SECONDS, build_rates_payload, parse_candles,
    parse_quotes, parse_symbols,
)
from .storage import CandleStore

log = logging.getLogger("mt5_market_data")


class MarketDataClient:
    """Connect to an MT5 web-terminal server and stream only market data.

    No account, order, position, or trade methods are implemented.
    """

    def __init__(
        self,
        login: int,
        password: str,
        server: str,
        symbols: list[str],
        timeframes: list[str] | None = None,
        database: str = "market_data.sqlite3",
        initial_bars: int = 1000,
        backfill_interval: float = 15.0,
        page_bars: int = 1000,
        verify_tls: bool = False,
    ):
        if not symbols:
            raise ValueError("At least one broker symbol is required")
        self.login = int(login)
        self.password = password
        self.server = server
        self.requested_symbols = list(dict.fromkeys(symbols))
        self.timeframes = list(dict.fromkeys(timeframes or ["M1"]))
        unknown = set(self.timeframes) - set(TIMEFRAME_SECONDS)
        if unknown:
            raise ValueError(f"Unsupported timeframes: {sorted(unknown)}")
        self.initial_bars = max(1, initial_bars)
        self.backfill_interval = max(2.0, backfill_interval)
        self.page_bars = max(100, page_bars)
        self.verify_tls = verify_tls

        self.store = CandleStore(database)
        self.connection: MT5Connection | None = None
        self.symbols: dict[str, Symbol] = {}
        self.symbols_by_id: dict[int, Symbol] = {}
        self.quotes: dict[str, Quote] = {}
        self.on_quote: Callable | None = None
        self.on_candle: Callable | None = None

        self._pending: dict[int, asyncio.Future] = {}
        self._command_locks = defaultdict(asyncio.Lock)
        self._receiver_task = None
        self._heartbeat_task = None
        self._history_task = None
        self._connection_lost = asyncio.Event()
        self._stop = asyncio.Event()

    async def _callback(self, callback, value):
        if callback is None:
            return
        try:
            result = callback(value)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("Market-data callback failed")

    async def _request(self, command_id: int, payload: bytes = b"", timeout: float = 15):
        async with self._command_locks[command_id]:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending[command_id] = future
            try:
                await self.connection.send(command_id, payload)
                response = await asyncio.wait_for(future, timeout)
                if response["res_code"] != 0:
                    raise RuntimeError(
                        f"MT5 command {command_id} failed with code {response['res_code']}"
                    )
                return response
            finally:
                if self._pending.get(command_id) is future:
                    self._pending.pop(command_id, None)

    async def _authenticate(self):
        await self.connection.send_auth()
        response = await self.connection.receive_auth()
        if response["cmd_id"] != 0 or response["res_code"] != 0:
            raise PermissionError("MT5 WebSocket session authentication failed")
        if len(response["res_body"]) < 98:
            raise ConnectionError("Authentication response did not contain a session key")
        self.connection.session_key = response["res_body"][66:]

    async def _login(self):
        payload = bytearray(912)
        password = self.password.encode("utf-16-le")
        if len(password) > 64:
            raise ValueError("MT5 password exceeds the researched 64-byte field")
        payload[4:4 + len(password)] = password
        server_bytes = self.server.encode("utf-16-le")
        if len(server_bytes) > 256:
            raise ValueError("Broker server name exceeds the researched 256-byte field")
        struct.pack_into("<I", payload, 476, len(server_bytes) // 2)
        payload[480:480 + len(server_bytes)] = server_bytes
        struct.pack_into("<Q", payload, 736, self.login)
        await self.connection.send(28, payload)
        response = await self.connection.receive(timeout=15)
        if response["cmd_id"] != 28 or response["res_code"] != 0:
            raise PermissionError("MT5 account login failed")

    async def connect(self):
        """Resolve the broker, authenticate, load symbols, and subscribe."""
        endpoints = await asyncio.to_thread(find_server_endpoints, self.server)
        failures = []
        for host, port in endpoints:
            connection = MT5Connection(host, port, verify_tls=self.verify_tls)
            try:
                self.connection = connection
                await connection.connect()
                await self._authenticate()
                await self._login()
                self._connection_lost.clear()
                self._receiver_task = asyncio.create_task(self._receive_loop())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                response = await self._request(34, timeout=25)
                self.symbols = parse_symbols(response["res_body"])
                self.symbols_by_id = {s.symbol_id: s for s in self.symbols.values()}

                missing = [name for name in self.requested_symbols if name not in self.symbols]
                if missing:
                    raise ValueError(
                        "Broker does not expose requested symbol(s): " + ", ".join(missing)
                    )

                ids = [self.symbols[name].symbol_id for name in self.requested_symbols]
                payload = struct.pack("<I", len(ids)) + b"".join(
                    struct.pack("<I", symbol_id) for symbol_id in ids
                )
                await connection.send(7, payload)
                log.info("Connected to %s:%s; subscribed to %s",
                         host, port, ", ".join(self.requested_symbols))
                return
            except Exception as exc:
                failures.append(f"{host}:{port}: {exc}")
                await self._disconnect()
        raise ConnectionError("All broker endpoints failed: " + " | ".join(failures))

    async def _receive_loop(self):
        try:
            while not self._stop.is_set():
                try:
                    message = await self.connection.receive(timeout=30)
                except asyncio.TimeoutError:
                    continue
                command_id = message["cmd_id"]
                if command_id == 8:
                    await self._handle_quotes(message["res_body"])
                elif command_id == 17:
                    # Symbol specification updates are not needed for OHLC history.
                    continue
                elif command_id in self._pending:
                    future = self._pending[command_id]
                    if not future.done():
                        future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("MT5 receive loop stopped")
            self._connection_lost.set()

    async def _handle_quotes(self, body: bytes):
        for quote in parse_quotes(body, self.symbols_by_id):
            self.quotes[quote.symbol] = quote
            await self._callback(self.on_quote, quote)

    async def _heartbeat_loop(self):
        try:
            while not self._stop.is_set():
                await asyncio.sleep(3)
                await self.connection.send(51)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("MT5 heartbeat failed")
            self._connection_lost.set()

    async def get_rates(self, symbol: str, timeframe: str, start: int, end: int) -> list[Candle]:
        """Request a broker candle range. Times are UTC Unix seconds."""
        if symbol not in self.symbols:
            raise KeyError(f"Unknown broker symbol: {symbol}")
        response = await self._request(
            11, build_rates_payload(symbol, timeframe, start, end), timeout=30
        )
        return parse_candles(response["res_body"], symbol, timeframe)

    async def _sync_history(self, symbol: str, timeframe: str):
        period = TIMEFRAME_SECONDS[timeframe]
        latest = self.store.latest_timestamp(symbol, timeframe)
        now = int(time.time())
        cursor = max(0, now - self.initial_bars * period) if latest is None else max(0, latest - period)

        # Request bounded ranges. Re-query one overlapping candle so the current
        # unfinished bar is refreshed and any just-completed bar is not skipped.
        while cursor < now and not self._stop.is_set() and not self._connection_lost.is_set():
            end = min(now, cursor + self.page_bars * period)
            candles = await self.get_rates(symbol, timeframe, cursor, end)
            if candles:
                self.store.upsert(candles)
                for candle in candles:
                    await self._callback(self.on_candle, candle)
            cursor = end
            await asyncio.sleep(0)
            if end >= now:
                break

    async def _history_loop(self):
        while not self._stop.is_set() and not self._connection_lost.is_set():
            started = time.monotonic()
            for symbol in self.requested_symbols:
                for timeframe in self.timeframes:
                    if self._stop.is_set() or self._connection_lost.is_set():
                        return
                    try:
                        await self._sync_history(symbol, timeframe)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("History sync failed for %s %s", symbol, timeframe)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1.0, self.backfill_interval - elapsed))

    def get_history(
        self, symbol: str, timeframe: str, limit: int = 500,
        start: int | None = None, end: int | None = None,
    ) -> list[Candle]:
        return self.store.get_candles(symbol, timeframe, limit, start, end)

    async def _disconnect(self):
        current = asyncio.current_task()
        for task_name in ("_history_task", "_heartbeat_task", "_receiver_task"):
            task = getattr(self, task_name)
            setattr(self, task_name, None)
            if task is not None and task is not current:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        if self.connection is not None:
            try:
                await self.connection.close()
            except Exception:
                pass
            self.connection = None
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def run_forever(self):
        """Keep the feed alive. Reconnects, resubscribes and resumes candle sync."""
        delay = 1.0
        try:
            while not self._stop.is_set():
                try:
                    await self.connect()
                    delay = 1.0
                    self._history_task = asyncio.create_task(self._history_loop())
                    lost_wait = asyncio.create_task(self._connection_lost.wait())
                    stop_wait = asyncio.create_task(self._stop.wait())
                    done, pending = await asyncio.wait(
                        (lost_wait, stop_wait), return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        if task is not lost_wait and not task.cancelled():
                            task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("MT5 connection attempt failed")
                finally:
                    await self._disconnect()
                if not self._stop.is_set():
                    log.warning("Reconnecting to %s in %.1f seconds", self.server, delay)
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    delay = min(delay * 2, 30.0)
        finally:
            await self._disconnect()

    async def stop(self):
        self._stop.set()
        self._connection_lost.set()
        if self.connection is not None:
            await self.connection.close()

    def close_store(self):
        self.store.close()
