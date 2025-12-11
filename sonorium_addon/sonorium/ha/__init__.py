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

__all__ = [
    "Speaker",
    "Area",
    "Floor",
    "SpeakerHierarchy",
    "HARegistry",
    "create_registry_from_supervisor",
]
