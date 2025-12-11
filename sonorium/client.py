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

        data = response.json().get("data", {})

        self = cls(
            device=device, 
            hostname=data['host'], 
            port=data['port'], 
            username=data['username'], 
            password=data['password'], 
            **kwargs
        )
        return self
