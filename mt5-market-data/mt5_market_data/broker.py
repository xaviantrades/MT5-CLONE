"""MetaQuotes broker-server discovery, based on the source research report."""
import hashlib
import json
import time
from urllib.parse import quote

import requests

HMAC_KEY = bytes([
    61, 123, 21, 22, 214, 234, 187, 52, 217, 214, 99, 227, 98, 62, 27, 215,
    251, 220, 174, 244, 87, 59, 223, 53, 127, 168, 207, 11, 190, 173, 146, 127,
])
SEARCH_URL = "https://search.mtapi.io/Search?company={company}&mt5=true"
SEARCHMQ_URL = "https://updates.metaquotes.net/public/mt5/network"
USER_AGENT = "MetaTrader 5 Terminal/5.5830"


def _signature(body: str) -> str:
    first = hashlib.md5(body.encode("latin-1")).digest()
    return hashlib.md5(first + HMAC_KEY).hexdigest()


def _cookie() -> str:
    now = int(time.time())
    ms = (time.monotonic_ns() // 1_000_000) & 0x1FFFFFF
    value = ((now - 1420070400) | (ms << 32)) | 0x4200000000000000
    seed = time.time_ns() & 0xFFFFFFFF
    sample = bytearray(256)
    for i in range(256):
        seed = (seed * 214013 + 2531011) & 0xFFFFFFFF
        sample[i] = (seed >> 16) & 0xFF
    digest = bytearray(hashlib.md5(sample).digest())
    digest[0] = sum(digest[1:]) & 0xFF
    tid = digest[:8].hex().upper()[:17]
    return f"_fz_uniq={value};uniq={value};age={now - 86400};tid={tid}"


def _search_mq(server: str) -> list[dict]:
    body = f"company={quote(server)}&code=mt5"
    form = f"{body}&signature={_signature(body)}&ver=2"
    response = requests.post(
        SEARCHMQ_URL,
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": USER_AGENT,
            "Cookie": _cookie(),
        },
        timeout=12,
    )
    response.raise_for_status()
    raw = response.text
    start = raw.find("{")
    if start < 0:
        raise ValueError("MetaQuotes broker search returned no JSON")
    return json.loads(raw[start:]).get("result", [])


def _search_fallback(server: str) -> list[dict]:
    response = requests.get(
        SEARCH_URL.format(company=quote(server)),
        timeout=12,
    )
    response.raise_for_status()
    return response.json().get("result", [])


def find_server_endpoints(server: str) -> list[tuple[str, int]]:
    """Return all matching broker access endpoints as (host, port)."""
    errors = []
    for search_fn in (_search_mq, _search_fallback):
        try:
            for company in search_fn(server):
                for item in company.get("results", []):
                    if item.get("name", "").casefold() != server.casefold():
                        continue
                    endpoints = []
                    for access in item.get("access", []):
                        host, _, port_text = access.rpartition(":")
                        if host:
                            endpoints.append((host, int(port_text or 443)))
                    if endpoints:
                        return list(dict.fromkeys(endpoints))
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(
        f"Could not resolve MT5 server {server!r}. " + " | ".join(errors)
    )
