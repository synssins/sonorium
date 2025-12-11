"""
Sonorium Home Assistant Integration Module

Handles communication with Home Assistant:
- Registry queries (floors, areas, speakers)
- MQTT entity management
- REST API calls for media player control
"""

from sonorium.ha.registry import (
    Speaker,
    Area,
    Floor,
    SpeakerHierarchy,
    HARegistry,
    create_registry_from_supervisor,
)

from sonorium.ha.media_controller import (
    HAMediaController,
    create_media_controller_from_supervisor,
)

__all__ = [
    # Registry
    "Speaker",
    "Area",
    "Floor",
    "SpeakerHierarchy",
    "HARegistry",
    "create_registry_from_supervisor",
    # Media Control
    "HAMediaController",
    "create_media_controller_from_supervisor",
]
