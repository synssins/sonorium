"""
Sonorium Settings - Configuration management.
Replaces fmtr.tools with standard pydantic settings.
"""
import asyncio
import json
import os
import socket
from pathlib import Path
from typing import ClassVar

import homeassistant_api
import httpx
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from sonorium.client import ClientSonorium
from sonorium.device import Sonorium
from sonorium.paths import PackagePaths, paths


# HA Constants (replaces fmtr.tools.ha.constants)
HA_URL_CORE_ADDON = "http://supervisor/core/api"
HA_URL_SUPERVISOR_ADDON = "http://supervisor"
HA_SUPERVISOR_TOKEN_KEY = "SUPERVISOR_TOKEN"


def get_host_ip_from_supervisor() -> str:
    """
    Get the host's LAN IP address from the HA Supervisor API.

    In Docker/addon environments, this returns the actual host IP
    that network speakers can reach, not the container's internal IP.
    """
    from sonorium.obs import logger

    try:
        # Get supervisor token from environment
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            logger.warning("SUPERVISOR_TOKEN not found in environment")
            return None

        logger.debug("Querying Supervisor API for network info...")
        # Query the Supervisor network info API
        response = httpx.get(
            "http://supervisor/network/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0
        )

        logger.debug(f"Supervisor API response: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            logger.debug(f"Network info data: {data}")
            # The response contains interfaces with their IPs
            # Look for the primary interface (usually eth0 or end0)
            interfaces = data.get("data", {}).get("interfaces", [])
            for iface in interfaces:
                iface_name = iface.get("interface", "")
                # Skip docker/hassio internal interfaces
                if iface_name.startswith(("docker", "hassio", "veth")):
                    logger.debug(f"Skipping internal interface: {iface_name}")
                    continue
                # Get IPv4 addresses
                ipv4_info = iface.get("ipv4", {})
                addresses = ipv4_info.get("address", [])
                logger.debug(f"Interface {iface_name} addresses: {addresses}")
                if addresses:
                    # Return first non-link-local address
                    for addr in addresses:
                        ip = addr.split("/")[0]  # Remove CIDR notation
                        if not ip.startswith("169.254."):  # Skip link-local
                            logger.info(f"Detected host IP from Supervisor: {ip}")
                            return ip
            logger.warning("No suitable IP found in Supervisor network info")
        else:
            logger.warning(f"Supervisor API returned {response.status_code}: {response.text[:200]}")
    except Exception as e:
        logger.warning(f"Failed to get IP from Supervisor API: {e}")
    return None


def get_local_ip() -> str:
    """
    Get the local network IP address for network speakers to connect to.

    Tries multiple methods:
    1. HA Supervisor API (for Docker/addon environments)
    2. UDP socket trick (for standalone environments)
    """
    from sonorium.obs import logger

    # First try Supervisor API (works in HA addon context)
    ip = get_host_ip_from_supervisor()
    if ip:
        return ip

    logger.debug("Supervisor API failed, trying UDP socket method...")

    # Fallback to UDP socket method (works in standalone context)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        logger.debug(f"UDP socket returned IP: {ip}")
        # Check if it's a Docker internal IP (172.x.x.x or 10.x.x.x ranges often used)
        # These won't be reachable from external devices
        if ip.startswith("172.") or ip.startswith("10."):
            logger.warning(f"Detected Docker internal IP: {ip} - speakers won't be able to reach this")
            return None  # Let caller handle fallback
        return ip
    except Exception as e:
        logger.warning(f"UDP socket method failed: {e}")
        return None


def apply_addon_env():
    """
    Apply environment variables from HA addon options.
    Replaces fmtr.tools.ha.apply_addon_env().
    """
    options_path = Path("/data/options.json")
    if options_path.exists():
        from sonorium.obs import logger
        logger.info(f'Converting addon "{options_path}" to environment variables...')
        try:
            with open(options_path) as f:
                options = json.load(f)
            for key, value in options.items():
                # Convert to environment variable format
                env_key = f"SONORIUM__{key.upper()}"
                if value is not None and value != "":
                    os.environ[env_key] = str(value)
        except Exception as e:
            logger.warning(f"Failed to load addon options: {e}")


class Settings(BaseSettings):
    """Sonorium configuration settings."""

    model_config = {"env_prefix": "SONORIUM__", "env_nested_delimiter": "__"}

    paths: ClassVar[PackagePaths] = paths

    ha_core_api: str = Field(default=HA_URL_CORE_ADDON)
    ha_supervisor_api: str = Field(default=HA_URL_SUPERVISOR_ADDON)

    token: str = Field(default="", alias=HA_SUPERVISOR_TOKEN_KEY)

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
        from sonorium.obs import logger

        logger.info(f"Resolving stream URL (input: {self.stream_url})...")
        local_ip = get_local_ip()
        logger.info(f"Detected local IP: {local_ip}")

        # Handle "auto" or empty - build URL from detected IP
        if not self.stream_url or self.stream_url.lower() == "auto":
            if local_ip:
                self.stream_url = f"http://{local_ip}:{self.stream_port}"
                logger.info(f"Auto-configured stream URL: {self.stream_url}")
            else:
                # Fallback if IP detection fails
                self.stream_url = f"http://127.0.0.1:{self.stream_port}"
                logger.error(f"IP detection failed! Using fallback: {self.stream_url}")
                logger.error("Network speakers will NOT be able to connect. Check Supervisor API access.")
        # Handle homeassistant.local - replace with detected IP
        elif 'homeassistant.local' in self.stream_url:
            if local_ip:
                self.stream_url = self.stream_url.replace('homeassistant.local', local_ip)
                logger.info(f"Replaced homeassistant.local with IP: {self.stream_url}")

        return self

    name: str = Sonorium.__name__

    # MQTT broker settings (auto-detect from Supervisor if not specified)
    mqtt_host: str = "auto"
    mqtt_port: int = 0  # 0 means auto-detect
    mqtt_username: str = ""
    mqtt_password: str = ""

    path_audio: str = str(paths.audio)

    def run(self):
        asyncio.run(self.run_async())

    async def run_async(self):
        from sonorium.obs import logger
        from sonorium.paths import paths
        from sonorium.version import __version__

        logger.info(f'Launching sonorium {__version__=} from entrypoint.')
        logger.info(f'Stream URL: {self.stream_url}')

        logger.info(f'Launching...')

        client_ha = homeassistant_api.Client(api_url=self.ha_core_api, token=self.token)
        device = Sonorium(
            client_ha=client_ha,
            path_audio_str=self.path_audio,
        )

        client = ClientSonorium.from_supervisor(device=device)
        await client.start()


# Apply addon environment variables before settings are loaded
apply_addon_env()
settings = Settings()
