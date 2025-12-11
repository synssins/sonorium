import asyncio

from pydantic import Field

from sonorium.client import ClientSonorium
from sonorium.device import Sonorium
from sonorium.paths import paths
from fmtr import tools
from fmtr.tools import sets, ha


class Settings(sets.Base):
    paths = paths

    ha_core_api: str = Field(default=ha.constants.URL_CORE_ADDON)
    ha_supervisor_api: str = Field(default=ha.constants.URL_SUPERVISOR_ADDON)

    token: str = Field(alias=ha.constants.SUPERVISOR_TOKEN_KEY)


    stream_url: str
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
