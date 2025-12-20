import asyncio
import os
import socket

import httpx
from pydantic import Field, model_validator

from sonorium.client import ClientSonorium
from sonorium.device import Sonorium
from sonorium.paths import paths
from fmtr import tools
from fmtr.tools import sets, ha


def get_host_ip_from_supervisor() -> str:
    """
    Get the host's LAN IP address from the HA Supervisor API.

    In Docker/addon environments, this returns the actual host IP
    that network speakers can reach, not the container's internal IP.
    """
    try:
        # Get supervisor token from environment
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return None

        # Query the Supervisor network info API
        response = httpx.get(
            "http://supervisor/network/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0
        )

        if response.status_code == 200:
            data = response.json()
            # The response contains interfaces with their IPs
            # Look for the primary interface (usually eth0 or end0)
            interfaces = data.get("data", {}).get("interfaces", [])
            for iface in interfaces:
                # Skip docker/hassio internal interfaces
                if iface.get("interface", "").startswith(("docker", "hassio", "veth")):
                    continue
                # Get IPv4 addresses
                ipv4_info = iface.get("ipv4", {})
                addresses = ipv4_info.get("address", [])
                if addresses:
                    # Return first non-link-local address
                    for addr in addresses:
                        ip = addr.split("/")[0]  # Remove CIDR notation
                        if not ip.startswith("169.254."):  # Skip link-local
                            return ip
    except Exception:
        pass
    return None


def get_local_ip() -> str:
    """
    Get the local network IP address for network speakers to connect to.

    Tries multiple methods:
    1. HA Supervisor API (for Docker/addon environments)
    2. UDP socket trick (for standalone environments)
    """
    # First try Supervisor API (works in HA addon context)
    ip = get_host_ip_from_supervisor()
    if ip:
        return ip

    # Fallback to UDP socket method (works in standalone context)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        # Check if it's a Docker internal IP (172.x.x.x or 10.x.x.x ranges often used)
        # These won't be reachable from external devices
        if ip.startswith("172.") or ip.startswith("10."):
            return None  # Let caller handle fallback
        return ip
    except Exception:
        return None


class Settings(sets.Base):
    paths = paths

    ha_core_api: str = Field(default=ha.constants.URL_CORE_ADDON)
    ha_supervisor_api: str = Field(default=ha.constants.URL_SUPERVISOR_ADDON)

    token: str = Field(alias=ha.constants.SUPERVISOR_TOKEN_KEY)


    stream_url: str = "auto"

    # Default streaming port (matches config.yaml ports mapping)
    stream_port: int = 8008

    @model_validator(mode='after')
    def resolve_stream_url(self):
        """
        Auto-detect stream URL using the local IP address.

        Network speakers (Sonos, etc.) can't resolve hostnames like
        'homeassistant.local', so we need to use the actual IP address.

        Handles:
        - "auto" or empty: Auto-detect IP and build URL
        - "homeassistant.local" in URL: Replace with detected IP
        - Any other URL: Use as-is (allows manual override)
        """
        local_ip = get_local_ip()

        # Handle "auto" or empty - build URL from detected IP
        if not self.stream_url or self.stream_url.lower() == "auto":
            if local_ip:
                self.stream_url = f"http://{local_ip}:{self.stream_port}"
            else:
                # Fallback if IP detection fails
                self.stream_url = f"http://127.0.0.1:{self.stream_port}"
        # Handle homeassistant.local - replace with detected IP
        elif 'homeassistant.local' in self.stream_url:
            if local_ip:
                self.stream_url = self.stream_url.replace('homeassistant.local', local_ip)

        return self

    name: str = Sonorium.__name__
    mqtt: tools.mqtt.Client.Args | None = None

    path_audio: str = str(paths.audio)

    def run(self):
        super().run()
        asyncio.run(self.run_async())

    async def run_async(self):
        from fmtr.tools import debug
        debug.trace()
        from fmtr import tools
        from sonorium.obs import logger
        from sonorium.paths import paths
        from sonorium.version import __version__

        logger.info(f'Launching {paths.name_ns} {__version__=} {tools.get_version()=} from entrypoint.')
        logger.debug(f'{paths.settings.exists()=} {str(paths.settings)=}')
        logger.info(f'Stream URL: {self.stream_url}')

        logger.info(f'Launching...')

        client_ha = ha.core.Client(api_url=self.ha_core_api, token=self.token)
        device = Sonorium(name=self.name, client_ha=client_ha, path_audio_str=self.path_audio, sw_version=__version__, manufacturer=paths.org_singleton, model=Sonorium.__name__)

        if self.mqtt:
            client = ClientSonorium.from_args(self.mqtt, device=device)
        else:
            client = ClientSonorium.from_supervisor(device=device)

        await client.start()


ha.apply_addon_env()
settings = Settings()
settings
