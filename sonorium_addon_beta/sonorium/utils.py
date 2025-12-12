"""
Shared utility functions for Sonorium
"""
import os

import httpx

from sonorium.obs import logger


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
