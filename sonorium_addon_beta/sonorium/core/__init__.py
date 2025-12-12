"""
Sonorium Core Module

Contains data models, state management, and managers.
"""

from sonorium.core.state import (
    NameSource,
    SonoriumSettings,
    SpeakerSelection,
    SpeakerGroup,
    Session,
    SonoriumState,
    StateStore,
)

from sonorium.core.session_manager import SessionManager
from sonorium.core.group_manager import GroupManager

__all__ = [
    # Data models
    "NameSource",
    "SonoriumSettings", 
    "SpeakerSelection",
    "SpeakerGroup",
    "Session",
    "SonoriumState",
    "StateStore",
    # Managers
    "SessionManager",
    "GroupManager",
]
