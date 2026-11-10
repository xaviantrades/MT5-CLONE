"""Only the MT5 WebSocket protocol pieces required for market data."""
import gzip
import random
import struct
import zlib
from datetime import datetime, timezone

from .models import Candle, Quote, Symbol

STATIC_KEY = bytes.fromhex(
    "02de02a1a65cc794684fcbea1ecb0fd7"
    "4ae657e43662c11eee885d2fd64f4964"
)
ZERO_IV = bytes(16)

TIMEFRAMES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 16385, "H4": 16388,
    "D1": 16408, "W1": 32769, "MN1": 49153,
}
TIMEFRAME_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
    "W1": 604800, "MN1": 2592000,
}
SYMBOL_RECORD_SIZE = 526
QUOTE_RECORD_SIZE = 50
CANDLE_RECORD_SIZE = 48


def aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    from Crypto.Cipher import AES
    padding = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([padding]) * padding
    return AES.new(key, AES.MODE_CBC, ZERO_IV).encrypt(padded)


def aes_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    from Crypto.Cipher import AES
    if not ciphertext or len(ciphertext) % 16:
        raise ValueError("AES ciphertext must be a non-empty multiple of 16 bytes")
    plaintext = AES.new(key, AES.MODE_CBC, ZERO_IV).decrypt(ciphertext)
    padding = plaintext[-1]
    if 1 <= padding <= 16 and plaintext.endswith(bytes([padding]) * padding):
        return plaintext[:-padding]
    raise ValueError("Invalid AES padding")


def build_command(command_id: int, payload: bytes = b"") -> bytes:
    return bytes((random.randrange(256), random.randrange(256))) + struct.pack("<H", command_id) + payload


def pack_wire(encrypted: bytes) -> bytes:
    return struct.pack("<II", len(encrypted), 1) + encrypted


def parse_response(data: bytes) -> dict:
    if len(data) < 5:
        raise ValueError("MT5 response is shorter than its 5-byte header")
    return {
        "tag": struct.unpack_from("<H", data, 0)[0],
        "cmd_id": struct.unpack_from("<H", data, 2)[0],
        "res_code": data[4],
        "res_body": data[5:],
    }


def decode_utf16(raw: bytes) -> str:
    return raw.decode("utf-16-le", errors="ignore").split("\x00", 1)[0]


def parse_symbols(body: bytes) -> dict[str, Symbol]:
    if len(body) < 8:
        raise ValueError("Symbol catalogue response is too short")
    compressed = body[4:]
    try:
        data = zlib.decompress(compressed)
    except zlib.error:
        data = zlib.decompress(compressed, -zlib.MAX_WBITS)
    if len(data) < 4:
        raise ValueError("Decompressed symbol catalogue is too short")

    count = struct.unpack_from("<I", data, 0)[0]
    offset = 4
    symbols = {}
    for _ in range(count):
        if offset + SYMBOL_RECORD_SIZE > len(data):
            raise ValueError("Truncated symbol catalogue")
        name = decode_utf16(data[offset:offset + 64])
        description = decode_utf16(data[offset + 64:offset + 192])
        digits = struct.unpack_from("<I", data, offset + 192)[0]
        symbol_id = struct.unpack_from("<I", data, offset + 196)[0]
        path = decode_utf16(data[offset + 200:offset + 456])
        if name:
            symbols[name] = Symbol(name, description, digits, symbol_id, path)
        offset += SYMBOL_RECORD_SIZE
    return symbols


def parse_quotes(body: bytes, symbols_by_id: dict[int, Symbol]) -> list[Quote]:
    quotes = []
    offset = 0
    while offset + QUOTE_RECORD_SIZE <= len(body):
        values = struct.unpack_from("<IiIdddqIH", body, offset)
        symbol_id, seconds, _fields, bid_raw, ask_raw, last_raw, tick_volume, ms_delta, flags = values
        symbol = symbols_by_id.get(symbol_id)
        if symbol is not None:
            scale = 10 ** symbol.digits
            timestamp = datetime.fromtimestamp(seconds + ms_delta / 1000, tz=timezone.utc)
            quotes.append(Quote(
                symbol=symbol.name,
                symbol_id=symbol_id,
                bid=bid_raw / scale,
                ask=ask_raw / scale,
                last=last_raw / scale,
                tick_volume=tick_volume,
                timestamp=timestamp,
                flags=flags,
            ))
        offset += QUOTE_RECORD_SIZE
    return quotes


def build_rates_payload(symbol: str, timeframe: str, start: int, end: int) -> bytes:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    encoded = symbol.encode("utf-16-le")
    if len(encoded) > 64:
        raise ValueError("Symbol name exceeds the 64-byte MT5 field")
    payload = bytearray(74)
    payload[:len(encoded)] = encoded
    struct.pack_into("<H", payload, 64, TIMEFRAMES[timeframe])
    struct.pack_into("<i", payload, 66, start)
    struct.pack_into("<i", payload, 70, end)
    return bytes(payload)


def parse_candles(body: bytes, symbol: str, timeframe: str) -> list[Candle]:
    if len(body) % CANDLE_RECORD_SIZE:
        raise ValueError(
            f"Rates response length {len(body)} is not divisible by {CANDLE_RECORD_SIZE}"
        )
    candles = []
    for offset in range(0, len(body), CANDLE_RECORD_SIZE):
        ts, open_, high, low, close, volume, spread = struct.unpack_from(
            "<i4dqi", body, offset
        )
        candles.append(Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            open=open_,
            high=high,
            low=low,
            close=close,
            tick_volume=volume,
            spread=spread,
        ))
    return candles
