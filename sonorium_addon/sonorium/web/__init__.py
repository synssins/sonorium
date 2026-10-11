"""
Sonorium Web Module

Provides REST API and web UI for managing Sonorium.
"""

from sonorium.web.api_v2 import create_api_router

__all__ = [
    "create_api_router",
]
