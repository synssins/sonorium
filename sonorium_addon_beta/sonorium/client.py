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
    @logger.instrument('Instantiating MQTT client from Supervisor API...')
    def from_supervisor(cls, device: Sonorium, **kwargs):
        from sonorium.settings import settings

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
        
        # Debug: log what we received
        logger.info(f"  MQTT service response: {response_json}")
        
        # Check if MQTT is available
        if not data:
            raise RuntimeError(
                "MQTT service not available. Please ensure the Mosquitto broker addon is installed "
                "and running, or that you have an MQTT broker configured in Home Assistant."
            )
        
        # Check for required keys
        required_keys = ['host', 'port', 'username', 'password']
        missing_keys = [k for k in required_keys if k not in data]
        if missing_keys:
            raise RuntimeError(
                f"MQTT configuration incomplete. Missing: {missing_keys}. "
                f"Available keys: {list(data.keys())}. "
                "Please check your MQTT broker configuration."
            )

        self = cls(
            device=device, 
            hostname=data['host'], 
            port=data['port'], 
            username=data['username'], 
            password=data['password'], 
            **kwargs
        )
        return self
