"""
Sonorium Plugin System

Provides the plugin architecture for extending Sonorium functionality.
"""

from sonorium.plugins.base import BasePlugin
from sonorium.plugins.speaker_base import SpeakerPlugin, NetworkSpeaker, SpeakerState
from sonorium.plugins.manager import PluginManager

__all__ = [
    'BasePlugin',
    'SpeakerPlugin',
    'NetworkSpeaker',
    'SpeakerState',
    'PluginManager'
]
