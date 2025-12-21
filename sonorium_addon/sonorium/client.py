import asyncio

from sonorium.api import ApiSonorium
from sonorium.device import Sonorium
from sonorium.obs import logger
from fmtr.tools import http
from haco.client import ClientHaco


class ClientSonorium(ClientHaco):
    """
    Take an extra API argument, and gather with super.start
    """

    API_CLASS = ApiSonorium

    def __init__(self, device: Sonorium, *args, **kwargs):
        super().__init__(device=device, *args, **kwargs)

    @logger.instrument('Connecting MQTT client to {self._client.username}@{self._hostname}:{self._port}...')
    async def start(self):
        # Start the base haco client and API
        await asyncio.gather(
            super().start(),
            self.API_CLASS.launch_async(self)
        )

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
                with http.Client() as client:
                    response = client.get(
                        f"{settings.ha_supervisor_api}/services/mqtt",
                        headers={
                            "Authorization": f"Bearer {settings.token}",
                            "Content-Type": "application/json",
                        },
                    )

                response_json = response.json()
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

        # Create client - username/password can be None for anonymous
        self = cls(
            device=device,
            hostname=mqtt_host,
            port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password,
            **kwargs
        )
        return self
