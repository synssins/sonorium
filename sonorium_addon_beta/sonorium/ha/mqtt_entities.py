"""
MQTT Entity Management for Sonorium Sessions

Creates and manages Home Assistant entities via MQTT Discovery:
- switch.sonorium_{session}_play - Play/pause toggle
- select.sonorium_{session}_theme - Theme selector
- number.sonorium_{session}_volume - Volume control
- sensor.sonorium_{session}_status - Current status
- sensor.sonorium_{session}_speakers - Speaker info
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable, Awaitable
from dataclasses import dataclass

from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.core.state import Session, StateStore
    from sonorium.core.session_manager import SessionManager


@dataclass
class EntityConfig:
    """Configuration for an MQTT entity."""
    component: str  # switch, select, number, sensor
    object_id: str  # unique id suffix
    name: str
    config: dict  # additional config


class SessionMQTTEntities:
    """
    Manages MQTT entities for a single session.
    
    Creates HA entities that mirror session state and allow control.
    """
    
    def __init__(
        self,
        session: Session,
        entity_prefix: str,
        mqtt_publish: Callable[[str, str, bool], Awaitable[None]],
        device_info: dict,
        themes: list[dict] = None,
    ):
        """
        Initialize entity manager for a session.
        
        Args:
            session: The session to create entities for
            entity_prefix: Prefix for entity IDs (e.g., "sonorium")
            mqtt_publish: Async function to publish MQTT messages
            device_info: HA device info dict for grouping entities
            themes: List of available themes for select entity
        """
        self.session = session
        self.prefix = entity_prefix
        self.mqtt_publish = mqtt_publish
        self.device_info = device_info
        self.themes = themes or []
        
        self.slug = session.get_entity_slug()
        self.base_topic = f"homeassistant"
        self.state_topic_base = f"{entity_prefix}/{self.slug}"
    
    def _get_unique_id(self, suffix: str) -> str:
        """Generate unique ID for an entity."""
        return f"{self.prefix}_{self.slug}_{suffix}"
    
    def _get_discovery_topic(self, component: str, suffix: str) -> str:
        """Generate MQTT discovery topic."""
        unique_id = self._get_unique_id(suffix)
        return f"{self.base_topic}/{component}/{unique_id}/config"
    
    async def publish_discovery(self):
        """Publish MQTT discovery configs for all session entities."""
        await self._publish_play_switch()
        await self._publish_theme_select()
        await self._publish_volume_number()
        await self._publish_status_sensor()
        await self._publish_speakers_sensor()
        
        logger.info(f"Published MQTT discovery for session '{self.session.name}'")
    
    async def remove_discovery(self):
        """Remove MQTT discovery configs (publish empty payloads)."""
        entities = [
            ("switch", "play"),
            ("select", "theme"),
            ("number", "volume"),
            ("sensor", "status"),
            ("sensor", "speakers"),
        ]
        
        for component, suffix in entities:
            topic = self._get_discovery_topic(component, suffix)
            await self.mqtt_publish(topic, "", retain=True)
        
        logger.info(f"Removed MQTT discovery for session '{self.session.name}'")
    
    async def update_state(self):
        """Publish current state for all entities."""
        # Play switch state
        await self.mqtt_publish(
            f"{self.state_topic_base}/play/state",
            "ON" if self.session.is_playing else "OFF",
            retain=True,
        )
        
        # Theme select state
        await self.mqtt_publish(
            f"{self.state_topic_base}/theme/state",
            self.session.theme_id or "",
            retain=True,
        )
        
        # Volume number state
        await self.mqtt_publish(
            f"{self.state_topic_base}/volume/state",
            str(self.session.volume),
            retain=True,
        )
        
        # Status sensor
        if self.session.is_playing and self.session.theme_id:
            theme_name = self._get_theme_name(self.session.theme_id)
            status = f"Playing: {theme_name}"
        else:
            status = "Stopped"
        await self.mqtt_publish(
            f"{self.state_topic_base}/status/state",
            status,
            retain=True,
        )
    
    def _get_theme_name(self, theme_id: str) -> str:
        """Get theme name from ID."""
        for theme in self.themes:
            if theme.get("id") == theme_id:
                return theme.get("name", theme_id)
        return theme_id
    
    async def _publish_play_switch(self):
        """Publish play/pause switch discovery."""
        unique_id = self._get_unique_id("play")
        config = {
            "name": f"{self.session.name} Play",
            "unique_id": unique_id,
            "object_id": f"{self.prefix}_{self.slug}_play",
            "state_topic": f"{self.state_topic_base}/play/state",
            "command_topic": f"{self.state_topic_base}/play/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:play-pause",
            "device": self.device_info,
        }
        
        topic = self._get_discovery_topic("switch", "play")
        await self.mqtt_publish(topic, json.dumps(config), retain=True)
    
    async def _publish_theme_select(self):
        """Publish theme selector discovery."""
        unique_id = self._get_unique_id("theme")
        
        # Build options list from themes
        options = [""] + [t.get("id", "") for t in self.themes if t.get("id")]
        
        config = {
            "name": f"{self.session.name} Theme",
            "unique_id": unique_id,
            "object_id": f"{self.prefix}_{self.slug}_theme",
            "state_topic": f"{self.state_topic_base}/theme/state",
            "command_topic": f"{self.state_topic_base}/theme/set",
            "options": options,
            "icon": "mdi:music-box-multiple",
            "device": self.device_info,
        }
        
        topic = self._get_discovery_topic("select", "theme")
        await self.mqtt_publish(topic, json.dumps(config), retain=True)
    
    async def _publish_volume_number(self):
        """Publish volume control discovery."""
        unique_id = self._get_unique_id("volume")
        config = {
            "name": f"{self.session.name} Volume",
            "unique_id": unique_id,
            "object_id": f"{self.prefix}_{self.slug}_volume",
            "state_topic": f"{self.state_topic_base}/volume/state",
            "command_topic": f"{self.state_topic_base}/volume/set",
            "min": 0,
            "max": 100,
            "step": 5,
            "unit_of_measurement": "%",
            "icon": "mdi:volume-high",
            "device": self.device_info,
        }
        
        topic = self._get_discovery_topic("number", "volume")
        await self.mqtt_publish(topic, json.dumps(config), retain=True)
    
    async def _publish_status_sensor(self):
        """Publish status sensor discovery."""
        unique_id = self._get_unique_id("status")
        config = {
            "name": f"{self.session.name} Status",
            "unique_id": unique_id,
            "object_id": f"{self.prefix}_{self.slug}_status",
            "state_topic": f"{self.state_topic_base}/status/state",
            "icon": "mdi:information-outline",
            "device": self.device_info,
        }
        
        topic = self._get_discovery_topic("sensor", "status")
        await self.mqtt_publish(topic, json.dumps(config), retain=True)
    
    async def _publish_speakers_sensor(self):
        """Publish speakers info sensor discovery."""
        unique_id = self._get_unique_id("speakers")
        config = {
            "name": f"{self.session.name} Speakers",
            "unique_id": unique_id,
            "object_id": f"{self.prefix}_{self.slug}_speakers",
            "state_topic": f"{self.state_topic_base}/speakers/state",
            "icon": "mdi:speaker-multiple",
            "device": self.device_info,
        }
        
        topic = self._get_discovery_topic("sensor", "speakers")
        await self.mqtt_publish(topic, json.dumps(config), retain=True)
    
    async def update_speakers_sensor(self, speaker_summary: str):
        """Update the speakers sensor with current selection."""
        await self.mqtt_publish(
            f"{self.state_topic_base}/speakers/state",
            speaker_summary,
            retain=True,
        )


class SonoriumMQTTManager:
    """
    Manages all MQTT entities for Sonorium.
    
    Handles:
    - Session entity creation/removal
    - Command handling (play, theme, volume)
    - State synchronization
    - Global entities (stop all, active count)
    """
    
    def __init__(
        self,
        state_store: StateStore,
        session_manager: SessionManager,
        mqtt_client,  # paho or fmtr.tools mqtt client
        entity_prefix: str = "sonorium",
    ):
        """
        Initialize the MQTT manager.
        
        Args:
            state_store: StateStore instance
            session_manager: SessionManager instance
            mqtt_client: MQTT client for publishing
            entity_prefix: Prefix for entity IDs
        """
        self.state = state_store
        self.session_manager = session_manager
        self.mqtt_client = mqtt_client
        self.prefix = entity_prefix
        
        # Track session entity managers
        self._session_entities: dict[str, SessionMQTTEntities] = {}
        
        # Device info for grouping entities
        self.device_info = {
            "identifiers": [f"{entity_prefix}_device"],
            "name": "Sonorium",
            "model": "Ambient Soundscape Mixer",
            "manufacturer": "Sonorium",
        }
        
        # Themes cache
        self._themes: list[dict] = []
    
    def set_themes(self, themes: list[dict]):
        """Update the available themes list."""
        self._themes = themes
    
    async def _mqtt_publish(self, topic: str, payload: str, retain: bool = False):
        """Publish an MQTT message."""
        try:
            if hasattr(self.mqtt_client, 'publish'):
                # paho-style client
                self.mqtt_client.publish(topic, payload, retain=retain)
            elif hasattr(self.mqtt_client, 'send'):
                # fmtr.tools style
                await self.mqtt_client.send(topic, payload, retain=retain)
            else:
                logger.warning(f"Unknown MQTT client type, cannot publish to {topic}")
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")
    
    async def initialize(self):
        """Initialize MQTT entities for all sessions."""
        logger.info("Initializing MQTT entities...")
        
        # Create entities for existing sessions
        for session in self.state.sessions.values():
            await self.add_session_entities(session)
        
        # Publish global entities
        await self._publish_global_entities()
        
        # Subscribe to command topics
        await self._subscribe_commands()
        
        logger.info(f"MQTT initialized with {len(self._session_entities)} sessions")
    
    async def add_session_entities(self, session: Session):
        """Add MQTT entities for a new session."""
        if session.id in self._session_entities:
            return
        
        entities = SessionMQTTEntities(
            session=session,
            entity_prefix=self.prefix,
            mqtt_publish=self._mqtt_publish,
            device_info=self.device_info,
            themes=self._themes,
        )
        
        await entities.publish_discovery()
        await entities.update_state()
        
        # Update speakers sensor
        speaker_summary = self.session_manager.get_speaker_summary(session)
        await entities.update_speakers_sensor(speaker_summary)
        
        self._session_entities[session.id] = entities
    
    async def remove_session_entities(self, session_id: str):
        """Remove MQTT entities for a deleted session."""
        if session_id not in self._session_entities:
            return
        
        entities = self._session_entities.pop(session_id)
        await entities.remove_discovery()
    
    async def update_session_state(self, session: Session):
        """Update state for a session's entities."""
        if session.id not in self._session_entities:
            await self.add_session_entities(session)
            return
        
        entities = self._session_entities[session.id]
        entities.session = session  # Update reference
        await entities.update_state()
        
        # Update speakers sensor
        speaker_summary = self.session_manager.get_speaker_summary(session)
        await entities.update_speakers_sensor(speaker_summary)
    
    async def _publish_global_entities(self):
        """Publish global Sonorium entities."""
        
        # Stop All switch
        config = {
            "name": "Sonorium Stop All",
            "unique_id": f"{self.prefix}_stop_all",
            "object_id": f"{self.prefix}_stop_all",
            "command_topic": f"{self.prefix}/stop_all/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:stop-circle",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/switch/{self.prefix}_stop_all/config",
            json.dumps(config),
            retain=True,
        )
        
        # Active Sessions sensor
        config = {
            "name": "Sonorium Active Sessions",
            "unique_id": f"{self.prefix}_active_sessions",
            "object_id": f"{self.prefix}_active_sessions",
            "state_topic": f"{self.prefix}/active_sessions/state",
            "icon": "mdi:counter",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/sensor/{self.prefix}_active_sessions/config",
            json.dumps(config),
            retain=True,
        )
        
        # Update active sessions count
        await self._update_active_sessions_count()
    
    async def _update_active_sessions_count(self):
        """Update the active sessions counter."""
        count = sum(1 for s in self.state.sessions.values() if s.is_playing)
        await self._mqtt_publish(
            f"{self.prefix}/active_sessions/state",
            str(count),
            retain=True,
        )
    
    async def _subscribe_commands(self):
        """Subscribe to command topics."""
        # Build list of topics to subscribe
        topics = [
            f"{self.prefix}/stop_all/set",
        ]
        
        # Add session-specific topics
        for session in self.state.sessions.values():
            slug = session.get_entity_slug()
            topics.extend([
                f"{self.prefix}/{slug}/play/set",
                f"{self.prefix}/{slug}/theme/set",
                f"{self.prefix}/{slug}/volume/set",
            ])
        
        # Subscribe (implementation depends on MQTT client type)
        try:
            if hasattr(self.mqtt_client, 'subscribe'):
                for topic in topics:
                    self.mqtt_client.subscribe(topic)
            elif hasattr(self.mqtt_client, 'on_message'):
                # Register message handler
                pass
        except Exception as e:
            logger.error(f"Failed to subscribe to topics: {e}")
    
    async def handle_command(self, topic: str, payload: str):
        """
        Handle an incoming MQTT command.
        
        Called by the MQTT client's message callback.
        """
        logger.info(f"MQTT command: {topic} = {payload}")
        
        # Stop all
        if topic == f"{self.prefix}/stop_all/set" and payload == "ON":
            await self.session_manager.stop_all()
            await self._update_active_sessions_count()
            return
        
        # Session commands
        for session_id, entities in self._session_entities.items():
            slug = entities.slug
            
            if topic == f"{self.prefix}/{slug}/play/set":
                if payload == "ON":
                    await self.session_manager.play(session_id)
                else:
                    await self.session_manager.pause(session_id)
                session = self.state.sessions.get(session_id)
                if session:
                    await self.update_session_state(session)
                await self._update_active_sessions_count()
                return
            
            elif topic == f"{self.prefix}/{slug}/theme/set":
                self.session_manager.update(session_id, theme_id=payload or None)
                session = self.state.sessions.get(session_id)
                if session:
                    await self.update_session_state(session)
                return
            
            elif topic == f"{self.prefix}/{slug}/volume/set":
                try:
                    volume = int(float(payload))
                    await self.session_manager.set_volume(session_id, volume)
                    session = self.state.sessions.get(session_id)
                    if session:
                        await self.update_session_state(session)
                except ValueError:
                    logger.warning(f"Invalid volume value: {payload}")
                return
    
    async def sync_all_states(self):
        """Synchronize all entity states with current session data."""
        for session in self.state.sessions.values():
            await self.update_session_state(session)
        await self._update_active_sessions_count()
