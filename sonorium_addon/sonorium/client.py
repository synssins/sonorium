"""
The running Sonorium behind the web app (Backend): the theme library and the
MQTT connection Home Assistant's entities go through, and how the broker is
found (find_mqtt_broker). MQTTClient is a small asyncio wrapper around
paho-mqtt, used by ha/mqtt_entities.py.
"""
import asyncio
import json
import urllib.error
import urllib.request
from typing import Awaitable, Callable, NamedTuple

import paho.mqtt.client as paho_mqtt

from sonorium import runtime
from sonorium.api import WebApp
from sonorium.device import Library
from sonorium.obs import logger

DEFAULT_MQTT_PORT = 1883
# The Supervisor's record of the MQTT service (the Mosquitto add-on registers it)
SUPERVISOR_MQTT_SERVICE = "/services/mqtt"
# Shown when no broker is set and the Supervisor knows none
NO_BROKER_HELP = (
    "MQTT host not configured. No MQTT broker is registered with the Supervisor. Either:\n"
    "  1. Install the Mosquitto broker addon in Home Assistant. If it is already\n"
    "     installed, make sure it is started and 'Start on boot' is on, then restart Sonorium, or\n"
    "  2. Set 'sonorium__mqtt_host' in addon configuration"
)


class MQTTClient:
    """
    Simple async wrapper around paho-mqtt client.

    Provides:
    - Async connect/publish/subscribe
    - Message callback routing
    - Integration with SonoriumMQTTManager
    """

    def __init__(
        self,
        hostname: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
    ):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password

        self._client = paho_mqtt.Client(paho_mqtt.CallbackAPIVersion.VERSION2)
        self._connected = asyncio.Event()
        self._message_handler: Callable[[str, str], Awaitable[None]] | None = None
        # Topics to (re)subscribe on every connect: with a clean session the
        # broker forgets subscriptions when it restarts
        self._topics: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Set up callbacks
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        # Set credentials if provided
        if username and password:
            self._client.username_pw_set(username, password)

    def __repr__(self) -> str:
        """Safe repr that never exposes password."""
        auth = "authenticated" if self.username else "anonymous"
        return f"MQTTClient({self.hostname}:{self.port}, {auth})"

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("  MQTT connected successfully")
            for topic in sorted(self._topics):
                client.subscribe(topic)
            # Use call_soon_threadsafe since this callback runs in paho's thread
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
            else:
                self._connected.set()
        else:
            logger.error(f"  MQTT connection failed: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        logger.warning(f"  MQTT disconnected: {reason_code}")
        # Use call_soon_threadsafe since this callback runs in paho's thread
        if self._loop:
            self._loop.call_soon_threadsafe(self._connected.clear)
        else:
            self._connected.clear()

    def _on_message(self, client, userdata, message):
        """Route incoming messages to the handler."""
        topic = message.topic
        payload = message.payload.decode('utf-8', errors='replace')

        if self._message_handler and self._loop:
            # Schedule the async handler on the main event loop (thread-safe)
            asyncio.run_coroutine_threadsafe(
                self._message_handler(topic, payload),
                self._loop
            )

    def set_message_handler(self, handler: Callable[[str, str], Awaitable[None]]):
        """Set the async message handler for incoming MQTT messages."""
        self._message_handler = handler

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def connect(self):
        """Connect to the MQTT broker."""
        logger.debug(f"Connecting to MQTT broker at {self.hostname}:{self.port}...")

        # Capture the event loop for thread-safe callbacks
        self._loop = asyncio.get_running_loop()

        self._client.connect_async(self.hostname, self.port)
        self._client.loop_start()

        # Wait for connection with timeout
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=30)
        except asyncio.TimeoutError:
            raise RuntimeError(f"MQTT connection timeout to {self.hostname}:{self.port}")

    async def disconnect(self):
        """Disconnect from the MQTT broker."""
        self._client.loop_stop()
        self._client.disconnect()

    async def close(self):
        """
        Drop the broker for good (MQTT removed in Settings > Connection):
        disconnect cleanly, stop paho's network loop so it doesn't reconnect,
        and forget the broker and credentials.
        """
        self._message_handler = None
        self._topics.clear()
        try:
            if self.hostname:
                self._client.disconnect()
        except Exception as e:
            logger.debug(f"  MQTT disconnect: {e}")
        # loop_stop joins paho's thread: run it off the event loop
        await asyncio.get_running_loop().run_in_executor(None, self._client.loop_stop)
        self._connected.clear()
        self.hostname = None
        self.username = None
        self.password = None
        logger.info("  MQTT disconnected (removed in Settings -> Connection)")

    def publish(self, topic: str, payload: str, retain: bool = False, qos: int = 1):
        """
        Publish a message (sync, for compatibility with existing code).
        Uses QoS 1 by default for reliable delivery.
        """
        result = self._client.publish(topic, payload, qos=qos, retain=retain)
        # Wait for publish to complete for QoS > 0
        if qos > 0:
            result.wait_for_publish(timeout=5.0)

    async def publish_async(self, topic: str, payload: str, retain: bool = False, qos: int = 1):
        """Publish a message asynchronously with reliable delivery."""
        result = self._client.publish(topic, payload, qos=qos, retain=retain)
        # Wait for publish confirmation in a thread-safe way
        if qos > 0 and self._loop:
            await self._loop.run_in_executor(
                None,
                lambda: result.wait_for_publish(timeout=5.0)
            )

    def subscribe(self, topic: str):
        """Subscribe to a topic (and again after any reconnect)."""
        if topic in self._topics:
            return
        self._topics.add(topic)
        self._client.subscribe(topic)
        logger.debug(f"  Subscribed to: {topic}")


class MqttBroker(NamedTuple):
    """Where to reach the MQTT broker. No host: run without MQTT (standalone only)."""
    host: str | None
    port: int = DEFAULT_MQTT_PORT
    username: str | None = None
    password: str | None = None


def _configured_broker(settings) -> MqttBroker:
    """The broker as the settings give it (run.sh: the add-on options, or bashio's MQTT service). Unset parts are None."""
    host = settings.mqtt_host
    if not host or host.lower() in ("auto", "none"):
        host = None
    secret = settings.mqtt_password.get_secret_value() if settings.mqtt_password else None
    return MqttBroker(host=host,
                      port=settings.mqtt_port if settings.mqtt_port and settings.mqtt_port > 0 else None,
                      username=settings.mqtt_username or None,
                      password=secret or None)


def _supervisor_mqtt_service(settings) -> dict | None:
    """The Supervisor's MQTT service record (GET /services/mqtt), or None. Problems are logged, not raised."""
    request = urllib.request.Request(
        settings.ha_supervisor_api + SUPERVISOR_MQTT_SERVICE,
        headers={"Authorization": f"Bearer {settings.token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            body = json.loads(reply.read().decode())
        service = body.get("data", body)  # the record, wrapped in "data" or not
        logger.info(f"  Supervisor API response: {list(service.keys()) if isinstance(service, dict) else 'invalid'}")
        if isinstance(service, dict) and service.get('host'):
            return service
        logger.warning("  Supervisor API returned no MQTT service data")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        logger.warning(f"  Supervisor API error: HTTP {e.code} - {e.reason} {detail}")
    except Exception as e:
        logger.warning(f"  Supervisor API fallback failed: {type(e).__name__}: {e}")
    return None


def find_mqtt_broker(settings) -> MqttBroker:
    """
    The broker Sonorium connects to. run.sh has usually filled the settings
    already (the add-on options, else bashio's MQTT service); if not, the
    Supervisor is asked directly. Standalone without a broker set, Sonorium
    runs without MQTT. In the add-on, no broker at all stops the start.
    """
    logger.debug("Finding the MQTT broker...")
    host, port, username, password = _configured_broker(settings)
    if host and not port:
        port = DEFAULT_MQTT_PORT
        logger.info(f"  MQTT host configured, using default port: {port}")
    if runtime.STANDALONE and not host:
        return MqttBroker(host=None)

    if not host:
        logger.info("  MQTT not configured, trying Supervisor API fallback...")
        service = _supervisor_mqtt_service(settings)
        if service is not None:
            host = service.get('host')
            port = service.get('port') or DEFAULT_MQTT_PORT
            # Credentials set in the options win over the service's
            if not username and 'username' in service:
                username = service.get('username')
            if not password and 'password' in service:
                password = service.get('password')
            logger.info(f"  Got MQTT config from Supervisor API: {host}:{port}")

    if not host:
        raise RuntimeError(NO_BROKER_HELP)
    if not port:
        port = DEFAULT_MQTT_PORT
        logger.info(f"  Using default MQTT port: {port}")
    logger.debug(f"  MQTT config: {host}:{port} ({'with credentials' if username else 'anonymous'})")
    return MqttBroker(host, port, username, password)


class Backend:
    """
    The running Sonorium: the theme library (`device`) and the MQTT link to
    Home Assistant. run() connects to the broker and then serves the web app
    (UI, API and streams) until it shuts down.
    """

    def __init__(self, device: Library, broker: MqttBroker):
        self.device = device
        self._mqtt = MQTTClient(hostname=broker.host, port=broker.port,
                                username=broker.username, password=broker.password)

    def __repr__(self) -> str:
        return f"Backend(mqtt={self._mqtt!r})"  # MQTTClient's repr never shows the password

    @property
    def mqtt_client(self) -> MQTTClient:
        """The MQTT connection, for the entity manager and Settings > Connection."""
        return self._mqtt

    async def run(self):
        logger.debug(f"MQTT: connecting to {self._mqtt.hostname}:{self._mqtt.port} "
                     f"as {self._mqtt.username or 'anonymous'}, then starting the web app...")
        if not runtime.STANDALONE:
            await self._mqtt.connect()  # the add-on needs its broker
        elif not self._mqtt.hostname:
            logger.info("  MQTT not configured (Settings -> Connection); Home Assistant entities disabled")
        else:
            # The web UI must come up even when the broker is down, so MQTT
            # can be fixed from it; paho keeps retrying meanwhile
            try:
                await self._mqtt.connect()
            except RuntimeError as e:
                logger.warning(f"  {e}; continuing without MQTT for now")
        await WebApp(self).serve()

    async def stop(self):
        await self._mqtt.disconnect()
