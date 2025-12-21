"""
MQTT Entity Management for Sonorium Sessions

Creates and manages Home Assistant entities via MQTT Discovery:
- switch.sonorium_{session}_play - Play/pause toggle
- select.sonorium_{session}_theme - Theme selector
- select.sonorium_{session}_preset - Preset selector (per-theme)
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
        get_presets_for_theme: Callable[[str], list[dict]] = None,
    ):
        """
        Initialize entity manager for a session.

        Args:
            session: The session to create entities for
            entity_prefix: Prefix for entity IDs (e.g., "sonorium")
            mqtt_publish: Async function to publish MQTT messages
            device_info: HA device info dict for grouping entities
            themes: List of available themes for select entity
            get_presets_for_theme: Callback to get presets for a theme ID
        """
        self.session = session
        self.prefix = entity_prefix
        self.mqtt_publish = mqtt_publish
        self.device_info = device_info
        self.themes = themes or []
        self.get_presets_for_theme = get_presets_for_theme

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
        import asyncio

        # Small delays between entity publications to prevent overwhelming
        # HA's MQTT discovery processor
        await self._publish_play_switch()
        await asyncio.sleep(0.05)
        await self._publish_theme_select()
        await asyncio.sleep(0.05)
        await self._publish_preset_select()
        await asyncio.sleep(0.05)
        await self._publish_volume_number()
        await asyncio.sleep(0.05)
        await self._publish_status_sensor()
        await asyncio.sleep(0.05)
        await self._publish_speakers_sensor()

        logger.info(f"Published MQTT discovery for session '{self.session.name}'")
    
    async def remove_discovery(self):
        """Remove MQTT discovery configs (publish empty payloads)."""
        entities = [
            ("switch", "play"),
            ("select", "theme"),
            ("select", "preset"),
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

        # Preset select state
        await self.mqtt_publish(
            f"{self.state_topic_base}/preset/state",
            self.session.preset_id or "",
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

    async def _publish_preset_select(self):
        """Publish preset selector discovery."""
        unique_id = self._get_unique_id("preset")

        # Get presets for the current theme
        options = [""]  # Empty option for "no preset"
        if self.session.theme_id and self.get_presets_for_theme:
            presets = self.get_presets_for_theme(self.session.theme_id)
            options.extend([p.get("id", "") for p in presets if p.get("id")])

        config = {
            "name": f"{self.session.name} Preset",
            "unique_id": unique_id,
            "object_id": f"{self.prefix}_{self.slug}_preset",
            "state_topic": f"{self.state_topic_base}/preset/state",
            "command_topic": f"{self.state_topic_base}/preset/set",
            "options": options,
            "icon": "mdi:tune-variant",
            "device": self.device_info,
        }

        topic = self._get_discovery_topic("select", "preset")
        await self.mqtt_publish(topic, json.dumps(config), retain=True)

    async def update_preset_options(self):
        """Re-publish preset select with updated options when theme changes."""
        await self._publish_preset_select()

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
    - Command handling (play, theme, volume, preset)
    - State synchronization
    - Global control entities (session selector + controls for selected session)
    - Global info entities (stop all, active count)
    """

    def __init__(
        self,
        state_store: StateStore,
        session_manager: SessionManager,
        mqtt_client,  # paho or fmtr.tools mqtt client
        entity_prefix: str = "sonorium",
        theme_metadata_manager=None,
    ):
        """
        Initialize the MQTT manager.

        Args:
            state_store: StateStore instance
            session_manager: SessionManager instance
            mqtt_client: MQTT client for publishing
            entity_prefix: Prefix for entity IDs
            theme_metadata_manager: ThemeMetadataManager for preset access
        """
        self.state = state_store
        self.session_manager = session_manager
        self.mqtt_client = mqtt_client
        self.prefix = entity_prefix
        self._theme_metadata_manager = theme_metadata_manager

        # Track session entity managers (per-session entities)
        self._session_entities: dict[str, SessionMQTTEntities] = {}

        # Track selected session for global controls
        self._selected_session_id: str | None = None
        
        # Session name to ID mapping for global controls
        self._session_name_to_id: dict[str, str] = {}

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

    def get_presets_for_theme(self, theme_id: str) -> list[dict]:
        """Get list of presets for a theme."""
        if not self._theme_metadata_manager:
            return []

        try:
            # Find the theme folder by ID
            folder = self._theme_metadata_manager.get_folder_for_id(theme_id)
            if not folder:
                return []

            metadata = self._theme_metadata_manager.get_metadata_by_folder(folder)
            if not metadata or not metadata.presets:
                return []

            # Convert presets dict to list format
            return [
                {"id": pid, "name": pdata.get("name", pid)}
                for pid, pdata in metadata.presets.items()
            ]
        except Exception as e:
            logger.warning(f"Failed to get presets for theme {theme_id}: {e}")
            return []
    
    async def _mqtt_publish(self, topic: str, payload: str, retain: bool = False):
        """Publish an MQTT message with logging."""
        import asyncio
        try:
            if hasattr(self.mqtt_client, 'publish'):
                # paho-style client - runs in executor to avoid blocking
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.mqtt_client.publish(topic, payload, retain=retain)
                )
                # Log entity config publishes at info level for debugging
                if "/config" in topic:
                    logger.info(f"  MQTT: Published entity config to {topic}")
            elif hasattr(self.mqtt_client, 'send'):
                # fmtr.tools style
                await self.mqtt_client.send(topic, payload, retain=retain)
            else:
                logger.warning(f"Unknown MQTT client type ({type(self.mqtt_client).__name__}), cannot publish to {topic}")
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")
    
    async def _clear_stale_entities(self):
        """
        Clear entity configurations from OLD addon versions that no longer exist.

        NOTE: We only delete entities with specific old names/IDs that are no longer
        used. We do NOT delete entities we're about to create (that caused race
        conditions). These old entities clutter the HA entity registry.
        """
        import asyncio
        logger.info("  Clearing stale entities from old addon versions...")

        # Known stale entities from old addon versions that need to be deleted
        # Format: (component, object_id)
        stale_entities = [
            # Old entity names from pre-v1.2.x versions
            ("button", f"{self.prefix}_play"),
            ("select", f"{self.prefix}_theme_2"),
            ("sensor", f"{self.prefix}_playback_state"),
            ("switch", f"{self.prefix}_2"),
            ("switch", f"{self.prefix}_enable_recording"),
            ("switch", f"{self.prefix}_paused"),
            ("switch", f"{self.prefix}_play_2"),
            ("switch", f"{self.prefix}_playing"),
            ("switch", f"{self.prefix}_theme_enabled"),
            ("update", f"{self.prefix}_update_2"),
            # Old global entities replaced with "global_" prefix in v1.2.37
            ("switch", f"{self.prefix}_play"),
            ("select", f"{self.prefix}_theme"),
            ("sensor", f"{self.prefix}_active_sessions"),
        ]

        for component, object_id in stale_entities:
            topic = f"homeassistant/{component}/{object_id}/config"
            # Empty payload deletes the entity from HA
            await self._mqtt_publish(topic, "", retain=True)

        logger.info(f"    Cleared {len(stale_entities)} stale entity configs")

        # Give HA time to process the deletions before creating new entities
        await asyncio.sleep(0.5)

    async def initialize(self):
        """Initialize MQTT entities for all sessions."""
        logger.info("Initializing MQTT entities...")

        # Clear stale entities first
        await self._clear_stale_entities()

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
            get_presets_for_theme=self.get_presets_for_theme,
        )

        await entities.publish_discovery()
        await entities.update_state()

        # Update speakers sensor
        speaker_summary = self.session_manager.get_speaker_summary(session)
        await entities.update_speakers_sensor(speaker_summary)

        self._session_entities[session.id] = entities
        
        # Update session selector options
        await self._update_session_selector_options()
    
    async def remove_session_entities(self, session_id: str):
        """Remove MQTT entities for a deleted session."""
        if session_id not in self._session_entities:
            return
        
        entities = self._session_entities.pop(session_id)
        await entities.remove_discovery()
        
        # If removed session was selected, clear selection
        if self._selected_session_id == session_id:
            self._selected_session_id = None
            await self._mqtt_publish(
                f"{self.prefix}/session/state",
                "",
                retain=True,
            )
            await self._update_global_control_states()
        
        # Update session selector options
        await self._update_session_selector_options()
    
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
        """Publish global Sonorium entities including session selector and controls."""
        import asyncio
        logger.info("  Publishing global entities...")

        # === SESSION SELECTOR ===
        # Dropdown to select which session to control (uses names, maps to IDs)
        session_options = [""]  # Empty = no selection
        self._session_name_to_id = {}  # Reset mapping
        
        for session in self.state.sessions.values():
            name = session.name or session.id
            session_options.append(name)
            self._session_name_to_id[name] = session.id
        
        config = {
            "name": "Sonorium Session",
            "unique_id": f"{self.prefix}_session",
            "object_id": f"{self.prefix}_session",
            "state_topic": f"{self.prefix}/session/state",
            "command_topic": f"{self.prefix}/session/set",
            "options": session_options,
            "icon": "mdi:playlist-music",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/select/{self.prefix}_session/config",
            json.dumps(config),
            retain=True,
        )
        logger.info("    Published: select.sonorium_session")

        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)

        # Publish initial session state (as name, not ID)
        selected_name = ""
        if self._selected_session_id:
            session = self.state.sessions.get(self._selected_session_id)
            if session:
                selected_name = session.name or session.id
        await self._mqtt_publish(
            f"{self.prefix}/session/state",
            selected_name,
            retain=True,
        )

        # Additional delay before next entity
        await asyncio.sleep(0.1)

        # === GLOBAL PLAY SWITCH ===
        # Controls play state of selected session
        # NOTE: Using "global_play" to avoid conflict with stuck old "play" entity
        config = {
            "name": "Sonorium Play",
            "unique_id": f"{self.prefix}_global_play",
            "object_id": f"{self.prefix}_global_play",
            "state_topic": f"{self.prefix}/play/state",
            "command_topic": f"{self.prefix}/play/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:play-pause",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/switch/{self.prefix}_global_play/config",
            json.dumps(config),
            retain=True,
        )
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)
        # Publish initial state
        await self._mqtt_publish(
            f"{self.prefix}/play/state",
            "OFF",
            retain=True,
        )
        await asyncio.sleep(0.1)

        # === GLOBAL THEME SELECT ===
        # NOTE: Using "global_theme" to avoid conflict with stuck old "theme" entity
        theme_options = [""]  # Empty = no theme
        for theme in self._themes:
            if "id" in theme:
                theme_options.append(theme["id"])
        logger.info(f"    Theme select options: {len(theme_options)} themes")

        config = {
            "name": "Sonorium Theme",
            "unique_id": f"{self.prefix}_global_theme",
            "object_id": f"{self.prefix}_global_theme",
            "state_topic": f"{self.prefix}/theme/state",
            "command_topic": f"{self.prefix}/theme/set",
            "options": theme_options,
            "icon": "mdi:music-box-multiple",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/select/{self.prefix}_global_theme/config",
            json.dumps(config),
            retain=True,
        )
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)
        # Publish initial state
        await self._mqtt_publish(
            f"{self.prefix}/theme/state",
            "",
            retain=True,
        )
        await asyncio.sleep(0.1)

        # === GLOBAL PRESET SELECT ===
        config = {
            "name": "Sonorium Preset",
            "unique_id": f"{self.prefix}_preset",
            "object_id": f"{self.prefix}_preset",
            "state_topic": f"{self.prefix}/preset/state",
            "command_topic": f"{self.prefix}/preset/set",
            "options": [""],  # Will be updated when session/theme changes
            "icon": "mdi:tune-variant",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/select/{self.prefix}_preset/config",
            json.dumps(config),
            retain=True,
        )
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)
        # Publish initial state
        await self._mqtt_publish(
            f"{self.prefix}/preset/state",
            "",
            retain=True,
        )
        await asyncio.sleep(0.1)

        # === GLOBAL VOLUME NUMBER ===
        config = {
            "name": "Sonorium Volume",
            "unique_id": f"{self.prefix}_volume",
            "object_id": f"{self.prefix}_volume",
            "state_topic": f"{self.prefix}/volume/state",
            "command_topic": f"{self.prefix}/volume/set",
            "min": 0,
            "max": 100,
            "step": 1,
            "unit_of_measurement": "%",
            "icon": "mdi:volume-high",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/number/{self.prefix}_volume/config",
            json.dumps(config),
            retain=True,
        )
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)
        # Publish initial state
        await self._mqtt_publish(
            f"{self.prefix}/volume/state",
            "50",
            retain=True,
        )
        await asyncio.sleep(0.1)

        # === GLOBAL STATUS SENSOR ===
        config = {
            "name": "Sonorium Status",
            "unique_id": f"{self.prefix}_status",
            "object_id": f"{self.prefix}_status",
            "state_topic": f"{self.prefix}/status/state",
            "icon": "mdi:information-outline",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/sensor/{self.prefix}_status/config",
            json.dumps(config),
            retain=True,
        )
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)
        # Publish initial state
        await self._mqtt_publish(
            f"{self.prefix}/status/state",
            "No session selected",
            retain=True,
        )
        await asyncio.sleep(0.1)

        # === GLOBAL SPEAKERS SENSOR ===
        config = {
            "name": "Sonorium Speakers",
            "unique_id": f"{self.prefix}_speakers",
            "object_id": f"{self.prefix}_speakers",
            "state_topic": f"{self.prefix}/speakers/state",
            "icon": "mdi:speaker-multiple",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/sensor/{self.prefix}_speakers/config",
            json.dumps(config),
            retain=True,
        )
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)
        # Publish initial state
        await self._mqtt_publish(
            f"{self.prefix}/speakers/state",
            "None",
            retain=True,
        )
        await asyncio.sleep(0.1)

        # === STOP ALL SWITCH ===
        # This is a momentary/command switch - set optimistic mode
        config = {
            "name": "Sonorium Stop All",
            "unique_id": f"{self.prefix}_stop_all",
            "object_id": f"{self.prefix}_stop_all",
            "state_topic": f"{self.prefix}/stop_all/state",
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
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)
        # Publish initial state (always OFF - it's a momentary action)
        await self._mqtt_publish(
            f"{self.prefix}/stop_all/state",
            "OFF",
            retain=True,
        )
        await asyncio.sleep(0.1)

        # === ACTIVE SESSIONS SENSOR ===
        # NOTE: Using "global_active_sessions" to avoid conflict with any stuck old entity
        config = {
            "name": "Sonorium Active Sessions",
            "unique_id": f"{self.prefix}_global_active_sessions",
            "object_id": f"{self.prefix}_global_active_sessions",
            "state_topic": f"{self.prefix}/active_sessions/state",
            "icon": "mdi:counter",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/sensor/{self.prefix}_global_active_sessions/config",
            json.dumps(config),
            retain=True,
        )
        # Wait for HA to process discovery config before publishing state
        await asyncio.sleep(0.1)

        logger.info("  Global entities published: session, play, theme, preset, volume, status, speakers, stop_all, active_sessions")

        # Update active sessions count (publishes initial state)
        await self._update_active_sessions_count()
        
        # Update global control states
        await self._update_global_control_states()
    
    async def _update_active_sessions_count(self):
        """Update the active sessions counter."""
        count = sum(1 for s in self.state.sessions.values() if s.is_playing)
        await self._mqtt_publish(
            f"{self.prefix}/active_sessions/state",
            str(count),
            retain=True,
        )

    async def _update_global_control_states(self):
        """Update global control entity states based on selected session."""
        session = None
        if self._selected_session_id:
            session = self.state.sessions.get(self._selected_session_id)
        
        if session:
            # Play state
            await self._mqtt_publish(
                f"{self.prefix}/play/state",
                "ON" if session.is_playing else "OFF",
                retain=True,
            )
            
            # Theme state
            await self._mqtt_publish(
                f"{self.prefix}/theme/state",
                session.theme_id or "",
                retain=True,
            )
            
            # Preset state
            await self._mqtt_publish(
                f"{self.prefix}/preset/state",
                session.preset_id or "",
                retain=True,
            )
            
            # Volume state
            await self._mqtt_publish(
                f"{self.prefix}/volume/state",
                str(session.volume),
                retain=True,
            )
            
            # Status
            status = "Playing" if session.is_playing else "Stopped"
            if session.theme_id:
                # Try to get theme name
                theme_name = session.theme_id
                for theme in self._themes:
                    if theme.get("id") == session.theme_id:
                        theme_name = theme.get("name", session.theme_id)
                        break
                status = f"{status} - {theme_name}"
            await self._mqtt_publish(
                f"{self.prefix}/status/state",
                status,
                retain=True,
            )
            
            # Speakers
            speaker_summary = self.session_manager.get_speaker_summary(session)
            await self._mqtt_publish(
                f"{self.prefix}/speakers/state",
                speaker_summary,
                retain=True,
            )
            
            # Update preset options for selected session's theme
            await self._update_global_preset_options(session.theme_id)
        else:
            # No session selected - show empty/default states
            await self._mqtt_publish(f"{self.prefix}/play/state", "OFF", retain=True)
            await self._mqtt_publish(f"{self.prefix}/theme/state", "", retain=True)
            await self._mqtt_publish(f"{self.prefix}/preset/state", "", retain=True)
            await self._mqtt_publish(f"{self.prefix}/volume/state", "50", retain=True)
            await self._mqtt_publish(f"{self.prefix}/status/state", "No session selected", retain=True)
            await self._mqtt_publish(f"{self.prefix}/speakers/state", "None", retain=True)
            await self._update_global_preset_options(None)
    
    async def _update_global_preset_options(self, theme_id: str | None):
        """Update the global preset select options based on theme."""
        options = [""]  # Empty option
        
        if theme_id:
            presets = self.get_presets_for_theme(theme_id)
            options.extend([p.get("id", "") for p in presets if p.get("id")])
        
        # Re-publish config with updated options
        config = {
            "name": "Sonorium Preset",
            "unique_id": f"{self.prefix}_preset",
            "object_id": f"{self.prefix}_preset",
            "state_topic": f"{self.prefix}/preset/state",
            "command_topic": f"{self.prefix}/preset/set",
            "options": options,
            "icon": "mdi:tune-variant",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/select/{self.prefix}_preset/config",
            json.dumps(config),
            retain=True,
        )
    
    async def _update_session_selector_options(self):
        """Update the session selector options when sessions change."""
        # Use session NAMES (not IDs) to be consistent with _publish_global_entities
        session_options = [""]  # Empty = no selection
        self._session_name_to_id = {}  # Reset mapping

        for session in self.state.sessions.values():
            name = session.name or session.id
            session_options.append(name)
            self._session_name_to_id[name] = session.id

        config = {
            "name": "Sonorium Session",
            "unique_id": f"{self.prefix}_session",
            "object_id": f"{self.prefix}_session",
            "state_topic": f"{self.prefix}/session/state",
            "command_topic": f"{self.prefix}/session/set",
            "options": session_options,
            "icon": "mdi:playlist-music",
            "device": self.device_info,
        }
        await self._mqtt_publish(
            f"homeassistant/select/{self.prefix}_session/config",
            json.dumps(config),
            retain=True,
        )
    
    async def _subscribe_commands(self):
        """Subscribe to command topics."""
        # Build list of topics to subscribe
        topics = [
            # Global control topics
            f"{self.prefix}/stop_all/set",
            f"{self.prefix}/session/set",
            f"{self.prefix}/play/set",
            f"{self.prefix}/theme/set",
            f"{self.prefix}/preset/set",
            f"{self.prefix}/volume/set",
        ]

        # Add session-specific topics
        for session in self.state.sessions.values():
            slug = session.get_entity_slug()
            topics.extend([
                f"{self.prefix}/{slug}/play/set",
                f"{self.prefix}/{slug}/theme/set",
                f"{self.prefix}/{slug}/preset/set",
                f"{self.prefix}/{slug}/volume/set",
            ])

        # Subscribe (implementation depends on MQTT client type)
        import asyncio
        try:
            if hasattr(self.mqtt_client, 'subscribe'):
                for topic in topics:
                    result = self.mqtt_client.subscribe(topic)
                    if asyncio.iscoroutine(result):
                        await result
        except Exception as e:
            logger.error(f"Failed to subscribe to topics: {e}")
    
    async def handle_command(self, topic: str, payload: str):
        """
        Handle an incoming MQTT command.
        
        Called by the MQTT client's message callback.
        """
        logger.info(f"MQTT command: {topic} = {payload}")
        
        # === GLOBAL COMMANDS ===
        
        # Stop all
        if topic == f"{self.prefix}/stop_all/set" and payload == "ON":
            await self.session_manager.stop_all()
            await self._update_active_sessions_count()
            await self._update_global_control_states()
            return
        
        # Session selector
        if topic == f"{self.prefix}/session/set":
            # Payload is session NAME, convert to ID using mapping
            if payload:
                new_session_id = self._session_name_to_id.get(payload)
                if not new_session_id:
                    logger.warning(f"Session name not found: {payload}")
                    return
            else:
                new_session_id = None

            self._selected_session_id = new_session_id

            # Publish state as NAME (not ID) to match select options
            selected_name = payload if payload else ""
            await self._mqtt_publish(
                f"{self.prefix}/session/state",
                selected_name,
                retain=True,
            )
            await self._update_global_control_states()
            return
        
        # Global play control (operates on selected session)
        if topic == f"{self.prefix}/play/set":
            if not self._selected_session_id:
                logger.warning("No session selected for global play control")
                return
            
            if payload == "ON":
                await self.session_manager.play(self._selected_session_id)
            else:
                await self.session_manager.pause(self._selected_session_id)
            
            session = self.state.sessions.get(self._selected_session_id)
            if session:
                await self.update_session_state(session)
            await self._update_active_sessions_count()
            await self._update_global_control_states()
            return
        
        # Global theme control
        if topic == f"{self.prefix}/theme/set":
            if not self._selected_session_id:
                logger.warning("No session selected for global theme control")
                return
            
            self.session_manager.update(self._selected_session_id, theme_id=payload or None)
            session = self.state.sessions.get(self._selected_session_id)
            if session:
                await self.update_session_state(session)
                # Update preset options in session entity
                if self._selected_session_id in self._session_entities:
                    await self._session_entities[self._selected_session_id].update_preset_options()
            await self._update_global_control_states()
            return
        
        # Global preset control
        if topic == f"{self.prefix}/preset/set":
            if not self._selected_session_id:
                logger.warning("No session selected for global preset control")
                return
            
            self.session_manager.update(self._selected_session_id, preset_id=payload or None)
            session = self.state.sessions.get(self._selected_session_id)
            if session:
                await self.update_session_state(session)
            await self._update_global_control_states()
            return
        
        # Global volume control
        if topic == f"{self.prefix}/volume/set":
            if not self._selected_session_id:
                logger.warning("No session selected for global volume control")
                return
            
            try:
                volume = int(float(payload))
                await self.session_manager.set_volume(self._selected_session_id, volume)
                session = self.state.sessions.get(self._selected_session_id)
                if session:
                    await self.update_session_state(session)
                await self._update_global_control_states()
            except ValueError:
                logger.warning(f"Invalid volume value: {payload}")
            return
        
        # === SESSION-SPECIFIC COMMANDS ===
        
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
                # Update global state if this is the selected session
                if session_id == self._selected_session_id:
                    await self._update_global_control_states()
                return
            
            elif topic == f"{self.prefix}/{slug}/theme/set":
                self.session_manager.update(session_id, theme_id=payload or None)
                session = self.state.sessions.get(session_id)
                if session:
                    await self.update_session_state(session)
                    # Update preset options when theme changes
                    await entities.update_preset_options()
                # Update global state if this is the selected session
                if session_id == self._selected_session_id:
                    await self._update_global_control_states()
                return

            elif topic == f"{self.prefix}/{slug}/preset/set":
                self.session_manager.update(session_id, preset_id=payload or None)
                session = self.state.sessions.get(session_id)
                if session:
                    await self.update_session_state(session)
                # Update global state if this is the selected session
                if session_id == self._selected_session_id:
                    await self._update_global_control_states()
                return

            elif topic == f"{self.prefix}/{slug}/volume/set":
                try:
                    volume = int(float(payload))
                    await self.session_manager.set_volume(session_id, volume)
                    session = self.state.sessions.get(session_id)
                    if session:
                        await self.update_session_state(session)
                    # Update global state if this is the selected session
                    if session_id == self._selected_session_id:
                        await self._update_global_control_states()
                except ValueError:
                    logger.warning(f"Invalid volume value: {payload}")
                return
    
    async def sync_all_states(self):
        """Synchronize all entity states with current session data."""
        for session in self.state.sessions.values():
            await self.update_session_state(session)
        await self._update_active_sessions_count()
