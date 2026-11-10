import struct
from datetime import timezone

from mt5_market_data.models import Symbol
from mt5_market_data.protocol import (
    CANDLE_RECORD_SIZE, QUOTE_RECORD_SIZE, aes_decrypt, aes_encrypt,
    build_command, build_rates_payload, parse_candles, parse_quotes, parse_response,
)


def test_aes_round_trip():
    key = bytes(range(32))
    payload = b"market-data-protocol-test"
    assert aes_decrypt(key, aes_encrypt(key, payload)) == payload


def test_command_header():
    command = build_command(11, b"abc")
    assert struct.unpack_from("<H", command, 2)[0] == 11
    assert command[4:] == b"abc"


def test_response_parser():
    raw = struct.pack("<HHB", 11, 11, 0) + b"rates"
    parsed = parse_response(raw)
    assert parsed["cmd_id"] == 11
    assert parsed["res_code"] == 0
    assert parsed["res_body"] == b"rates"


def test_rates_payload_layout():
    payload = build_rates_payload("EURUSDm", "M1", 100, 200)
    assert len(payload) == 74
    assert struct.unpack_from("<Hii", payload, 64) == (1, 100, 200)


def test_parse_candle_record():
    timestamp = 1_700_000_000
    body = struct.pack("<i4dqi", timestamp, 1.1, 1.2, 1.0, 1.15, 123, 8)
    assert len(body) == CANDLE_RECORD_SIZE
    candle = parse_candles(body, "EURUSDm", "M1")[0]
    assert candle.timestamp == timestamp
    assert candle.close == 1.15
    assert candle.tick_volume == 123


def test_parse_quote_record():
    symbol = Symbol("EURUSDm", "Euro vs US Dollar", 5, 42)
    body = struct.pack("<IiIdddqIH", 42, 1_700_000_000, 0, 110000, 110010, 110005, 9, 250, 3)
    assert len(body) == QUOTE_RECORD_SIZE
    quote = parse_quotes(body, {42: symbol})[0]
    assert quote.bid == 1.1
    assert quote.ask == 1.1001
    assert quote.timestamp.tzinfo == timezone.utc
