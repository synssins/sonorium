"""
Trying a connection before it's saved (Settings > Connection > Edit > Test):
Home Assistant's API with a URL and token, or an MQTT broker with a login.
Nothing is kept; each answers {"success", "message"} in words for the user.
"""
from __future__ import annotations

import asyncio
import threading

TIMEOUT_SECONDS = 8


async def test_ha(url: str, token: str, client_factory=None) -> dict:
    url = (url or "").strip().rstrip("/")
    if not url.lower().startswith(("http://", "https://")):
        return {"success": False, "message": "The URL must start with http:// or https://"}
    if not token:
        return {"success": False, "message": "An access token is needed"}
    import httpx
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=TIMEOUT_SECONDS))
    try:
        async with factory() as client:
            response = await client.get(f"{url}/api/config", headers={"Authorization": f"Bearer {token}"})
    except Exception as e:
        return {"success": False, "message": f"No answer from {url} ({type(e).__name__})"}
    if response.status_code == 401:
        return {"success": False, "message": "Home Assistant refused the token"}
    if response.status_code != 200:
        return {"success": False, "message": f"Home Assistant answered {response.status_code}"}
    try:
        version = response.json().get("version")
    except Exception:
        version = None
    return {"success": True, "message": f"Connected: Home Assistant {version}" if version else "Connected"}


async def test_mqtt(host: str, port, username=None, password=None) -> dict:
    host = (host or "").strip()
    if not host:
        return {"success": False, "message": "A broker address is needed"}
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"success": False, "message": "Port must be 1–65535"}

    def attempt() -> dict:
        import paho.mqtt.client as paho_mqtt
        done = threading.Event()
        result = {"success": False, "message": "No answer from the broker"}

        def on_connect(client, userdata, flags, reason_code, properties=None):
            if reason_code == 0:
                result.update(success=True, message="Connected")
            else:
                result.update(message=f"The broker refused: {reason_code}")
            done.set()

        client = paho_mqtt.Client(paho_mqtt.CallbackAPIVersion.VERSION2)
        if username:
            client.username_pw_set(username, password or None)
        client.on_connect = on_connect
        try:
            client.connect_async(host, port)
            client.loop_start()
            done.wait(TIMEOUT_SECONDS)
        except Exception as e:
            result.update(message=f"No answer from {host}:{port} ({type(e).__name__})")
        finally:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass
        return result

    return await asyncio.to_thread(attempt)
