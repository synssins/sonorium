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
        self._api_instance = None

    @logger.instrument('Connecting MQTT client to {self._client.username}@{self._hostname}:{self._port}...')
    async def start(self):
        # Create API instance
        self._api_instance = self.API_CLASS(self)
        
        # Start the base haco client and API
        await asyncio.gather(
            super().start(),
            self._launch_api_with_v2_init()
        )
    
    async def _launch_api_with_v2_init(self):
        """Launch the API and then initialize v2 components."""
        # Start the API server in background
        api_task = asyncio.create_task(self._api_instance.launch_async_instance())
        
        # Wait a moment for API to be ready, then initialize v2
        await asyncio.sleep(2)
        
        try:
            self._api_instance.initialize_v2()
        except Exception as e:
            logger.error(f"Failed to initialize v2: {e}")
        
        # Wait for API task to complete (it won't unless server stops)
        await api_task

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
