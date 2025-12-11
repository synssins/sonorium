from dataclasses import dataclass

from sonorium.obs import logger
from sonorium.utils import call_ha_service

from haco.control import Control
from haco.number import Number
from haco.select import Select
from haco.sensor import Sensor
from haco.switch import Switch


@dataclass(kw_only=True)
class ThemeRelativeControl(Control):

    @property
    def themes(self):
        return self.device.themes

    @property
    def theme(self):
        current = self.themes.current
        if current is None and len(self.themes) > 0:
            self.themes.current = self.themes[0]
            current = self.themes.current
        return current


@dataclass(kw_only=True)
class SelectTheme(Select, ThemeRelativeControl):
    icon: str = 'surround-sound'
    name: str = 'Theme'

    @logger.instrument('Setting Theme to "{value}"...')
    async def command(self, value):
        theme = self.themes.name[value]
        self.themes.current = theme
        # Enable all recordings in newly selected theme
        for instance in theme.instances:
            instance.is_enabled = True
        await self.device.sns_url.state()
        return value

    async def state(self, value=None):
        theme = self.theme
        if theme is None:
            return None
        return theme.name


@dataclass(kw_only=True)
class NumberMasterVolume(Number):
    """Master volume control for the entire Sonorium mix output"""
    icon: str = 'volume-high'
    name: str = 'Master Volume'
    min: float = 0
    max: float = 100
    step: float = 5

    @logger.instrument('Setting master volume to {value}...')
    async def command(self, value):
        # Convert 0-100 to gain multiplier (0-10.0 range, with 60 = 6.0 default)
        self.device.master_volume = value / 100 * 10.0
        logger.info(f'Master volume set to {value}% (gain: {self.device.master_volume:.2f})')

    async def state(self, value=None):
        # Convert gain back to 0-100 percentage
        return int(self.device.master_volume / 10.0 * 100)


@dataclass(kw_only=True)
class SelectMediaPlayer(Select, ThemeRelativeControl):
    icon: str = 'cast-audio'
    name: str = 'Media Player'

    @logger.instrument('Selecting Media Player "{value}"...')
    async def command(self, value):
        state = self.device.media_player_states.friendly_name[value]
        self.device.media_player_states.current = state
        logger.info(f'Set current media player to: {state.entity_id} ({state.friendly_name})')
        return value

    async def state(self, value):
        player = self.device.media_player_states.current
        if player:
            return player.friendly_name
        return None


@dataclass(kw_only=True)
class StreamURL(Sensor, ThemeRelativeControl):
    icon: str = 'link-variant'
    name: str = 'Stream URL'

    async def state(self, value=None):
        theme = self.theme
        if theme:
            return theme.url
        return None


@dataclass(kw_only=True)
class PlayPauseSwitch(Switch, ThemeRelativeControl):
    """
    Play/Pause toggle switch:
    - OFF (False) = Stopped or Paused - ready to play
    - ON (True) = Currently playing
    
    Toggle ON: Start/resume playback
    Toggle OFF: Pause playback (keeps position, can resume)
    """
    icon: str = 'play-pause'
    name: str = 'Play'

    async def command(self, value):
        theme = self.theme
        state = self.device.media_player_states.current
        
        if value:  # Turn ON = Start/Resume playing
            if not theme:
                logger.error('PlayPauseSwitch: No theme selected!')
                return
            if not state:
                logger.error('PlayPauseSwitch: No media player selected!')
                return
            
            # Enable all recordings in the theme
            for instance in theme.instances:
                instance.is_enabled = True
            
            entity_id = state.entity_id
            stream_url = theme.url
            
            logger.info(f'PlayPauseSwitch: Starting/resuming playback on {entity_id}')
            
            try:
                call_ha_service(
                    domain="media_player",
                    service="play_media",
                    service_data={
                        "entity_id": entity_id,
                        "media_content_id": stream_url,
                        "media_content_type": "music"
                    }
                )
                self.device.playback_state = 'playing'
            except Exception as e:
                logger.error(f'PlayPauseSwitch: Error: {e}')
                
        else:  # Turn OFF = Pause
            if state:
                entity_id = state.entity_id
                logger.info(f'PlayPauseSwitch: Pausing {entity_id}')
                try:
                    call_ha_service(
                        domain="media_player",
                        service="media_pause",
                        service_data={"entity_id": entity_id}
                    )
                    self.device.playback_state = 'paused'
                except Exception as e:
                    logger.error(f'PlayPauseSwitch: Error pausing: {e}')

    async def state(self, value=None):
        # ON when playing, OFF when paused or stopped
        return self.device.playback_state == 'playing'
