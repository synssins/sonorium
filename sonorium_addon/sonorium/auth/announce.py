"""
Announce Sonorium on the LAN with mDNS, so the app can find it:

    _sonorium._tcp.local.   TXT: id, name, api, http, https, pair, version, url

Only a hint: anyone on the LAN can announce anything, so the app trusts a
server only after linking (the QR code carries the certificate fingerprint).
The Home Assistant add-on isn't on the host network, so its announcement
wouldn't reach the LAN: there the app links with the QR code (which carries
the address). Docker (host networking) and the native installs announce.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from sonorium.obs import logger

SERVICE_TYPE = "_sonorium._tcp.local."


class Announcer:
    """start() and stop() block (zeroconf's own thread): call them with asyncio.to_thread."""

    def __init__(self):
        self._zc = None
        self._info = None

    def start(self, *, server_id: str, version: str, api: int, base_url: str, https_port: int, login_required: bool) -> bool:
        url = urlsplit(base_url or "")
        host = url.hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            logger.debug(f"mDNS: no IP address in {base_url!r}; Sonorium isn't announced")
            return False
        if address.is_loopback:
            return False
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            logger.debug("mDNS: zeroconf isn't installed; Sonorium isn't announced")
            return False
        http_port = url.port or 8008
        properties = {"id": server_id, "name": "Sonorium", "api": str(api), "http": str(http_port),
                      "https": str(https_port or ""), "pair": "1" if login_required else "0",
                      "version": version, "url": f"{url.scheme or 'http'}://{url.netloc}"}
        name = f"Sonorium {server_id[:8]}.{SERVICE_TYPE}"
        try:
            self._info = ServiceInfo(SERVICE_TYPE, name, addresses=[address.packed], port=http_port,
                                     properties=properties, server=f"sonorium-{server_id[:8]}.local.")
            self._zc = Zeroconf(interfaces=[str(address)])
            self._zc.register_service(self._info, allow_name_change=True)
            logger.info(f"mDNS: Sonorium announced as {SERVICE_TYPE} at {address}:{http_port}")
            return True
        except Exception as e:
            logger.warning(f"mDNS: Sonorium could not be announced ({e})")
            self.stop()
            return False

    def stop(self) -> None:
        zc, info = self._zc, self._info
        self._zc = self._info = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
            zc.close()
        except Exception:
            pass

