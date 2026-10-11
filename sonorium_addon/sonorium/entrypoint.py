"""
Starts Sonorium: `python -m sonorium.entrypoint`, as the add-on's run.sh and
the Docker image's entrypoint.sh do.
"""
import asyncio
import sys
import traceback


async def start_sonorium() -> None:
    """Load the theme library, find the MQTT broker, then run until the web server stops."""
    from sonorium.client import Backend, find_mqtt_broker
    from sonorium.device import Library
    from sonorium.obs import logger
    from sonorium.settings import settings
    from sonorium.utils import HomeAssistantClient
    from sonorium.version import __version__

    logger.info(f"Starting Sonorium {__version__}")
    logger.debug(f"Stream URL: {settings.stream_url}")
    logger.debug(f"Max channels: {settings.max_channels}")

    home_assistant = HomeAssistantClient(api_url=settings.ha_core_api, token=settings.token)
    library = Library(client_ha=home_assistant, audio_dir=settings.path_audio)
    backend = Backend(library, find_mqtt_broker(settings))
    await backend.run()


def main():
    try:
        asyncio.run(start_sonorium())
    except Exception as e:
        print(f"FATAL: Failed to start Sonorium: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
