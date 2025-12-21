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
    Replaces fmtr.tools.iterator_tools.IndexList.
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


def call_ha_service(domain: str, service: str, service_data: dict):
    """Call Home Assistant service using direct REST API"""
    token = os.environ.get('SUPERVISOR_TOKEN')
    
    if not token:
        logger.warning("No SUPERVISOR_TOKEN available - running outside HA?")
        return None
    
    url = f"http://supervisor/core/api/services/{domain}/{service}"
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
