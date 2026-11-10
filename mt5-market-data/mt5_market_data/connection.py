"""Encrypted WebSocket transport for the MT5 web-terminal protocol."""
import asyncio
import ssl

import websockets

from .protocol import STATIC_KEY, aes_decrypt, aes_encrypt, build_command, pack_wire, parse_response


class MT5Connection:
    def __init__(self, host: str, port: int = 443, verify_tls: bool = False):
        self.host = host
        self.port = port
        self.verify_tls = verify_tls
        self.websocket = None
        self.session_key = None
        self._send_lock = asyncio.Lock()

    async def connect(self):
        context = ssl.create_default_context()
        if not self.verify_tls:
            # The researched broker endpoints use IP addresses with a certificate
            # whose hostname does not match that IP. Enable verify_tls where the
            # broker provides a matching certificate or trusted proxy hostname.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        url = f"wss://{self.host}:{self.port}/terminal"
        self.websocket = await websockets.connect(
            url,
            ssl=context,
            ping_interval=None,
            open_timeout=15,
            close_timeout=5,
            additional_headers={"Origin": f"https://{self.host}:{self.port}"},
            max_size=8 * 1024 * 1024,
        )

    async def _send(self, key: bytes, command_id: int, payload: bytes):
        async with self._send_lock:
            encrypted = aes_encrypt(key, build_command(command_id, payload))
            await self.websocket.send(pack_wire(encrypted))

    async def send_auth(self):
        await self._send(STATIC_KEY, 0, bytes(64))

    async def send(self, command_id: int, payload: bytes = b""):
        if self.session_key is None:
            raise RuntimeError("Session key has not been negotiated")
        await self._send(self.session_key, command_id, payload)

    async def _receive(self, key: bytes, timeout: float) -> dict:
        raw = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
        if not isinstance(raw, bytes) or len(raw) < 8:
            raise ConnectionError("Unexpected MT5 WebSocket frame")
        declared_length = int.from_bytes(raw[:4], "little")
        if declared_length > len(raw) - 8:
            raise ConnectionError("Truncated MT5 WebSocket frame")
        return parse_response(aes_decrypt(key, raw[8:8 + declared_length]))

    async def receive_auth(self, timeout: float = 12) -> dict:
        return await self._receive(STATIC_KEY, timeout)

    async def receive(self, timeout: float = 30) -> dict:
        if self.session_key is None:
            raise RuntimeError("Session key has not been negotiated")
        return await self._receive(self.session_key, timeout)

    async def close(self):
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None
