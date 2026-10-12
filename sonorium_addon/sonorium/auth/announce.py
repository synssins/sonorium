"""
Announce Sonorium on the LAN with mDNS, so the app and browsers can find it:

    sonorium.local          the server's LAN addresses (sonorium-2.local, ... when the name is taken)
    _sonorium._tcp.local.   TXT: id, name, api, http, https, pair, version, url, host
                            (pair: "1" while Require login is on; it follows the setting)

Only a hint: anyone on the LAN can announce anything, so the app trusts a
server only after linking (the QR code carries the certificate fingerprint).
The Home Assistant add-on isn't on the host network, so its announcement
wouldn't reach the LAN: there the app links with the QR code (which carries
the address). Docker (host networking) and the native installs announce.

lan_addresses() is also what the Link App QR code lists in h=.
"""
from __future__ import annotations

import ipaddress
import os
import re
import threading
from typing import Callable, Iterable, Optional, Union
from urllib.parse import urlsplit

from sonorium.obs import logger

SERVICE_TYPE = "_sonorium._tcp.local."
HOSTNAME = "sonorium"
MAX_HOSTNAMES = 9          # sonorium.local, sonorium-2.local ... sonorium-9.local
RESOLVE_TIMEOUT_MS = 1500  # how long to listen for another device answering for a name

# Interfaces that never face the LAN: loopback, Docker / Podman / libvirt / CNI bridges and their pairs
_SKIP_INTERFACES = re.compile(r"^(lo\d*|docker\d*|veth\w*|br-\w+|virbr\d+|cni\d*|flannel\S*|podman\d*|hassio|kube\S*|incusbr\d*|lxdbr\d*|lxcbr\d*|tailscale\d*|zt\w*|wg\d*)$",
                              re.IGNORECASE)
# Docker's default bridge and Home Assistant's internal network
_SKIP_NETWORKS = (ipaddress.ip_network("172.17.0.0/16"), ipaddress.ip_network("172.30.32.0/23"))


def hostnames() -> list[str]:
    """The .local names Sonorium tries, in order."""
    return [f"{HOSTNAME}.local"] + [f"{HOSTNAME}-{n}.local" for n in range(2, MAX_HOSTNAMES + 1)]


def _usable(ip: str, interface: str = "") -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if address.version != 4 or address.is_loopback or address.is_link_local or address.is_unspecified \
            or address.is_multicast:
        return False
    if any(address in net for net in _SKIP_NETWORKS):
        return False
    return not _SKIP_INTERFACES.match(interface or "")


def _supervisor_addresses() -> list[tuple[str, str]]:
    """Add-on: the host's own interfaces, from the Supervisor (the container only sees HA's internal network)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return []
    import httpx
    response = httpx.get("http://supervisor/network/info", headers={"Authorization": f"Bearer {token}"}, timeout=5.0)
    out = []
    for iface in (response.json().get("data") or {}).get("interfaces") or []:
        if not iface.get("enabled", True) or iface.get("connected") is False:
            continue
        for cidr in ((iface.get("ipv4") or {}).get("address") or []):
            out.append((str(iface.get("interface") or ""), str(cidr).split("/")[0]))
    return out


def interface_addresses() -> list[tuple[str, str]]:
    """(interface, IPv4 address) of this machine (the add-on: of the HA host)."""
    from sonorium import runtime
    try:
        if not runtime.STANDALONE:
            return _supervisor_addresses()
        import ifaddr  # comes with zeroconf
    except Exception:
        return []
    out = []
    try:
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str):  # IPv4 (IPv6 is a tuple)
                    out.append((str(adapter.nice_name or adapter.name), ip.ip))
    except Exception:
        return []
    return out


def lan_addresses(first: str = "", *, interfaces: Optional[Iterable[tuple[str, str]]] = None) -> list[str]:
    """
    Every usable LAN IPv4 address of the server, `first` (the address speakers
    are given) first. When `first` is an IP that isn't one of this machine's
    addresses, Sonorium isn't on the host network (Docker bridge): only `first`
    is returned, since the container's own addresses don't reach the LAN.
    """
    if interfaces is None:
        interfaces = interface_addresses()
    found = []
    for name, ip in interfaces:
        if _usable(ip, name) and ip not in found:
            found.append(ip)
    first = (first or "").strip()
    if not first:
        return found
    try:
        is_ip = bool(ipaddress.ip_address(first))
    except ValueError:
        is_ip = False
    if is_ip and found and first not in found:
        return [first]
    return [first] + [ip for ip in found if ip != first]


class Announcer:
    """start() and stop() block (zeroconf's own thread): call them with asyncio.to_thread."""

    def __init__(self, *, addresses: Callable[[str], list[str]] = lan_addresses, pair_check_seconds: float = 10.0):
        self._zc = None
        self._info = None
        self._addresses = addresses
        self.hostname: Optional[str] = None  # "sonorium.local" once announced
        self._login_required: Callable[[], bool] = lambda: False
        self._pair_check_seconds = pair_check_seconds
        self._stop_watch = threading.Event()
        self._watcher: Optional[threading.Thread] = None

    def _pair(self) -> str:
        """TXT pair: "1" while Require login is on (sign-in needed), else "0"."""
        try:
            return "1" if self._login_required() else "0"
        except Exception:
            return "0"

    def refresh_pair(self) -> bool:
        """Re-announce when TXT pair no longer matches Require login. True when it changed."""
        zc, info = self._zc, self._info
        if zc is None or info is None:
            return False
        pair = self._pair()
        text = lambda s: s.decode() if isinstance(s, bytes) else s  # noqa: E731  (zeroconf keeps bytes)
        properties = {text(k): text(v) for k, v in (info.properties or {}).items()}
        if properties.get("pair") == pair:
            return False
        from zeroconf import ServiceInfo
        properties["pair"] = pair
        new = ServiceInfo(info.type, info.name, addresses=info.addresses, port=info.port,
                          properties=properties, server=info.server)
        zc.update_service(new)
        self._info = new
        logger.debug(f"mDNS: pair is now {pair}")
        return True

    def _watch(self) -> None:
        while not self._stop_watch.wait(self._pair_check_seconds):
            try:
                self.refresh_pair()
            except Exception as e:
                logger.debug(f"mDNS: could not update pair ({e})")

    def _resolve(self, name: str) -> list[str]:
        """The IPv4 addresses another responder gives for `name` (empty: nobody answered)."""
        from zeroconf import AddressResolverIPv4
        resolver = AddressResolverIPv4(name)
        if resolver.request(self._zc, RESOLVE_TIMEOUT_MS):
            return resolver.parsed_addresses()
        return []

    def _pick_hostname(self, own: set[str]) -> Optional[str]:
        """The first of sonorium.local, sonorium-2.local, ... that no other device answers for."""
        for name in hostnames():
            try:
                answer = set(self._resolve(f"{name}."))
            except Exception as e:
                logger.debug(f"mDNS: could not check {name} ({e})")
                answer = set()
            if not answer or answer <= own:  # nobody, or this machine (a restart)
                return name
            logger.info(f"mDNS: {name} is taken by {', '.join(sorted(answer))}")
        return None

    def start(self, *, server_id: str, version: str, api: int, base_url: str, https_port: int,
              login_required: Union[bool, Callable[[], bool]]) -> bool:
        """login_required: Require login now (a callable is checked again every few seconds: TXT pair follows it)."""
        self._login_required = login_required if callable(login_required) else (lambda: bool(login_required))
        url = urlsplit(base_url or "")
        ips = [ip for ip in self._addresses(url.hostname or "") if _usable(ip)]
        if not ips:
            logger.debug(f"mDNS: no LAN address for {base_url!r}; Sonorium isn't announced")
            return False
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            logger.debug("mDNS: zeroconf isn't installed; Sonorium isn't announced")
            return False
        http_port = url.port or 8008
        try:
            self._zc = Zeroconf(interfaces=ips)
            hostname = self._pick_hostname(set(ips))
            server = f"{hostname}." if hostname else f"sonorium-{server_id[:8]}.local."
            properties = {"id": server_id, "name": "Sonorium", "api": str(api), "http": str(http_port),
                          "https": str(https_port or ""), "pair": self._pair(),
                          "version": version, "url": f"{url.scheme or 'http'}://{url.netloc}",
                          "host": hostname or ""}
            self._info = ServiceInfo(SERVICE_TYPE, f"Sonorium {server_id[:8]}.{SERVICE_TYPE}",
                                     addresses=[ipaddress.ip_address(ip).packed for ip in ips], port=http_port,
                                     properties=properties, server=server)
            self._zc.register_service(self._info, allow_name_change=True)
            self.hostname = hostname
            if callable(login_required):  # Require login can change while Sonorium runs
                self._stop_watch.clear()
                self._watcher = threading.Thread(target=self._watch, name="mdns-pair", daemon=True)
                self._watcher.start()
            if hostname:
                logger.info(f"mDNS: Sonorium announced as {hostname} ({', '.join(ips)}), "
                            f"http://{hostname}:{http_port}" + (f" and https://{hostname}:{https_port}" if https_port else ""))
            else:
                logger.warning(f"mDNS: {hostnames()[0]} to {hostnames()[-1]} are all taken; "
                               f"Sonorium is announced as {SERVICE_TYPE} at {', '.join(ips)} without a name")
            return True
        except Exception as e:
            logger.warning(f"mDNS: Sonorium could not be announced ({e})")
            self.stop()
            return False

    def stop(self) -> None:
        self._stop_watch.set()
        zc, info = self._zc, self._info
        self._zc = self._info = None
        self.hostname = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
            zc.close()
        except Exception:
            pass
