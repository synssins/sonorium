"""
Sonorium MQTT Client - paho-mqtt based client for Home Assistant integration.

Replaces the HACO-based client with a simpler paho-mqtt implementation
that integrates with SonoriumMQTTManager for HA entity management.
"""
import asyncio
from typing import Callable, Awaitable

import paho.mqtt.client as paho_mqtt

from sonorium.api import ApiSonorium
from sonorium.device import Sonorium
from sonorium.obs import logger


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
        self._loop: asyncio.AbstractEventLoop | None = None

        # Set up callbacks
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        # Set credentials if provided
        if username and password:
            self._client.username_pw_set(username, password)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("  MQTT connected successfully")
            # Use call_soon_threadsafe since this callback runs in paho's thread
            if self._loop:
                self._loop.call_soon_threadsafe(self._connected.set)
            else:
                self._connected.set()
        else:
            logger.error(f"  MQTT connection failed: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        logger.warning(f"  MQTT disconnected: {reason_code}")
        self._connected.clear()

    def _on_message(self, client, userdata, message):
        """Route incoming messages to the handler."""
        topic = message.topic
        payload = message.payload.decode('utf-8', errors='replace')

        if self._message_handler:
            # Schedule the async handler
            asyncio.create_task(self._message_handler(topic, payload))

    def set_message_handler(self, handler: Callable[[str, str], Awaitable[None]]):
        """Set the async message handler for incoming MQTT messages."""
        self._message_handler = handler

    async def connect(self):
        """Connect to the MQTT broker."""
        logger.info(f"Connecting to MQTT broker at {self.hostname}:{self.port}...")

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

    def publish(self, topic: str, payload: str, retain: bool = False):
        """
        Publish a message (sync, for compatibility with existing code).
        Returns immediately, message is queued.
        """
        self._client.publish(topic, payload, retain=retain)

    async def publish_async(self, topic: str, payload: str, retain: bool = False):
        """Publish a message asynchronously."""
        self._client.publish(topic, payload, retain=retain)

    def subscribe(self, topic: str):
        """Subscribe to a topic."""
        self._client.subscribe(topic)
        logger.debug(f"  Subscribed to: {topic}")


class ClientSonorium:
    """
    Sonorium client that manages MQTT connection and API server.

    Replaces the HACO-based ClientHaco with a simpler paho-mqtt implementation.
    """

    API_CLASS = ApiSonorium

    def __init__(
        self,
        device: Sonorium,
        hostname: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
    ):
        self.device = device
        self._mqtt = MQTTClient(
            hostname=hostname,
            port=port,
            username=username,
            password=password,
        )

    @property
    def mqtt_client(self) -> MQTTClient:
        """Get the MQTT client for entity managers."""
        return self._mqtt

    @logger.instrument('Connecting MQTT client to {self._mqtt.username}@{self._mqtt.hostname}:{self._mqtt.port}...')
    async def start(self):
        """Start the MQTT client and API server."""
        # Connect to MQTT broker
        await self._mqtt.connect()

        # Launch the API server
        await self.API_CLASS.launch_async(self)

    async def stop(self):
        """Stop the client."""
        await self._mqtt.disconnect()

    @classmethod
    @logger.instrument('Instantiating MQTT client...')
    def from_supervisor(cls, device: Sonorium, **kwargs):
        """
        Create MQTT client with auto-detection from Supervisor API.

        Configuration priority:
        1. Use addon config values if explicitly set (not "auto"/0/empty)
        2. Otherwise, auto-detect from HA Supervisor API (/services/mqtt)
        3. Username/password are optional (allows anonymous connections)
        """
        import urllib.request
        import json
        from sonorium.settings import settings

        # Start with config values
        mqtt_host = settings.mqtt_host if settings.mqtt_host and settings.mqtt_host.lower() != "auto" else None
        mqtt_port = settings.mqtt_port if settings.mqtt_port and settings.mqtt_port > 0 else None
        mqtt_username = settings.mqtt_username if settings.mqtt_username else None
        mqtt_password = settings.mqtt_password if settings.mqtt_password else None

        # If host/port not configured, fetch from Supervisor API
        if not mqtt_host or not mqtt_port:
            logger.info("  MQTT host/port not configured, fetching from Supervisor API...")
            try:
                url = f"{settings.ha_supervisor_api}/services/mqtt"
                req = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {settings.token}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    response_json = json.loads(response.read().decode())

                data = response_json.get("data", {})
                logger.info(f"  MQTT service response: {response_json}")

                if data:
                    # Use Supervisor values for missing config
                    if not mqtt_host:
                        mqtt_host = data.get('host')
                    if not mqtt_port:
                        mqtt_port = data.get('port')
                    # Only use Supervisor credentials if not configured and available
                    if not mqtt_username and 'username' in data:
                        mqtt_username = data.get('username')
                    if not mqtt_password and 'password' in data:
                        mqtt_password = data.get('password')
                else:
                    logger.warning("  MQTT service not available from Supervisor")
            except Exception as e:
                logger.warning(f"  Failed to fetch MQTT config from Supervisor: {e}")

        # Validate we have at least host and port
        if not mqtt_host:
            raise RuntimeError(
                "MQTT host not configured. Either:\n"
                "  1. Install the Mosquitto broker addon in Home Assistant, or\n"
                "  2. Set 'sonorium__mqtt_host' in addon configuration"
            )
        if not mqtt_port:
            mqtt_port = 1883  # Default MQTT port
            logger.info(f"  Using default MQTT port: {mqtt_port}")

        # Log final configuration (mask password)
        auth_status = "with credentials" if mqtt_username else "anonymous"
        logger.info(f"  MQTT config: {mqtt_host}:{mqtt_port} ({auth_status})")

        # Create client
        return cls(
            device=device,
            hostname=mqtt_host,
            port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password,
            **kwargs
        )
