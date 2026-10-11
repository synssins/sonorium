"""
Shared utility functions for Sonorium
"""
import os
import re

import httpx

from sonorium.obs import logger


class IndexList(list):
    """
    Simple list subclass that supports attribute-based indexing.
    """

    def __init__(self, iterable=None):
        super().__init__(iterable or [])
        self.current = None

    def __getattr__(self, name):
        """Allow attribute-style access to create dict views."""
        if name.startswith('_'):
            raise AttributeError(name)

        # Return a dict mapping the attribute value to the item
        result = {}
        for item in self:
            if hasattr(item, name):
                key = getattr(item, name)
                result[key] = item
        return result


def sanitize(text: str) -> str:
    """Sanitize a string to be safe for use as an ID/filename."""
    # Replace spaces and special chars with underscores
    text = re.sub(r'[^\w\-]', '_', text.lower())
    # Remove consecutive underscores
    text = re.sub(r'_+', '_', text)
    # Strip leading/trailing underscores
    return text.strip('_')


_ID_SEPARATORS = re.compile(r'[\s\-_]+')


def sanitize_id(*strings: str, sep: str = '-') -> str:
    """
    A theme folder name as the dashed ID older themes were known by
    ("Rain & Thunder" -> "rain-thunder"). Keeps letters and digits (any
    script), lowercased, and turns each run of spaces, dashes and underscores
    into one `sep`. Gives exactly what Amniotic-era Sonorium gave, so those IDs
    keep matching (tests/test_sanitize_id.py).
    """
    text = ' '.join(string for string in strings if string)
    text = ''.join(c.lower() for c in text if c.isalnum() or c == ' ')
    return _ID_SEPARATORS.sub(sep, text).strip()


class HomeAssistantClient:
    """Home Assistant's REST API: the entity states (GET /api/states)."""

    TIMEOUT = 10.0

    def __init__(self, api_url: str, token: str | None):
        self.api_url = api_url.rstrip('/')
        self.token = token

    def get_states(self) -> list[dict]:
        """Every entity's state, as Home Assistant returns it (entity_id, state, attributes, ...)."""
        response = httpx.get(
            f"{self.api_url}/states",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            timeout=self.TIMEOUT,
        )
        response.raise_for_status()
        return response.json()


def call_ha_service(domain: str, service: str, service_data: dict):
    """Call Home Assistant service using direct REST API"""
    from sonorium.settings import settings
    token = settings.token

    if not token:
        logger.warning("No Home Assistant token available")
        return None

    url = f"{settings.ha_core_api}/services/{domain}/{service}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    logger.info(f'Calling HA service: {domain}.{service}')
    
    try:
        response = httpx.post(url, json=service_data, headers=headers, timeout=5.0)
        logger.info(f'Response status: {response.status_code}')
        return response.json() if response.text else None
    except httpx.TimeoutException:
        logger.info('Service call sent (response timed out, but command was delivered)')
        return None
    except Exception as e:
        logger.error(f'Service call error: {e}')
        return None
