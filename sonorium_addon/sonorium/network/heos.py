"""Minimal HEOS CLI client (Denon/Marantz): JSON lines over TCP port 1255."""
from __future__ import annotations

import asyncio
import json

HEOS_PORT = 1255


async def heos_command(host: str, command: str, timeout: float = 5.0) -> dict:
    """
    Send one HEOS CLI command, e.g. "player/get_players", and return its JSON
    response. Skips unrelated events and "command under process" interim replies.
    """
    loop = asyncio.get_running_loop()
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, HEOS_PORT), timeout)
    try:
        writer.write(f"heos://{command}\r\n".encode())
        await writer.drain()
        expected = command.split("?", 1)[0]
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"No HEOS response to {expected}")
            line = await asyncio.wait_for(reader.readline(), remaining)
            if not line:
                raise ConnectionError("HEOS connection closed")
            try:
                data = json.loads(line.decode("utf-8", errors="ignore"))
            except ValueError:
                continue
            heos = data.get("heos", {}) if isinstance(data, dict) else {}
            if heos.get("command") != expected:
                continue
            if "command under process" in str(heos.get("message", "")):
                continue
            return data
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def heos_succeeded(response: dict) -> bool:
    return response.get("heos", {}).get("result") == "success"
