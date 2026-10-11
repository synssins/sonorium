"""
Finding the server: the Link App QR payload (a sonorium.app web address, the
older sonorium:// form still parsed), the LAN addresses it lists, and the
sonorium.local name with its fallback when another device has it (zeroconf
mocked: no network).
"""
import sys
import types
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.auth.announce", "sonorium.runtime") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture(scope="module")
def qr(modules):
    pytest.importorskip("segno")
    with addon_modules("sonorium.auth.qr") as mods:
        yield mods.qr


def link(qr, **over):
    args = dict(server_id="0f8fad5b-d9cb-469f-a165-70867728950e", name="Sonorium",
                hosts=["192.168.1.5", "10.0.0.7", "sonorium.local"], http_port=8008, https_port=8808,
                fingerprint="ab" * 32, code="K7Q2-9XWD")
    args.update(over)
    return qr.pair_uri(**args)


# --- QR payload ---------------------------------------------------------------

def test_link_is_a_web_address_with_everything_after_the_hash(qr):
    uri = link(qr, username="chris")
    base, fragment = uri.split("#", 1)
    assert base == "https://sonorium.app/link" and "?" not in base
    keys = [part.split("=", 1)[0] for part in fragment.split("&")]
    assert keys == ["v", "id", "n", "h", "p", "s", "fp", "c", "u"]
    p = qr.parse_link(uri)
    assert p == {"v": "1", "id": "0f8fad5b-d9cb-469f-a165-70867728950e", "n": "Sonorium",
                 "h": "192.168.1.5,10.0.0.7,sonorium.local", "p": "8008", "s": "8808",
                 "fp": "ab" * 32, "c": "K7Q2-9XWD", "u": "chris"}
    assert "&h=192.168.1.5,10.0.0.7,sonorium.local&" in uri  # commas stay readable


def test_link_without_https_or_user_and_with_odd_characters(qr):
    uri = link(qr, https_port=0, name="Living room & co", username="a b#c")
    p = qr.parse_link(uri)
    assert "s" not in p and p["n"] == "Living room & co" and p["u"] == "a b#c"
    assert uri.count("#") == 1  # a # in a value is escaped
    assert "u" not in qr.parse_link(link(qr))


def test_older_sonorium_pair_form_still_parses(qr):
    new = qr.parse_link(link(qr))
    legacy = "sonorium://pair?" + link(qr).split("#", 1)[1]
    assert qr.parse_link(legacy) == new
    assert qr.parse_link("https://sonorium.app/link/#" + link(qr).split("#", 1)[1]) == new
    assert qr.parse_link("https://evil.example/link#v=1&c=x") == {}
    assert qr.parse_link("") == {}


def test_qr_svg_carries_the_link(qr):
    uri = link(qr, username="chris")
    svg = qr.qr_svg(uri)
    assert svg.startswith("<svg") and "data:image/png;base64," in svg
    import segno
    code = segno.make(uri, error="h", micro=False)
    assert code.error == "H" and code.version <= 15  # still easy to scan from a screen
    assert unquote(uri.split("#", 1)[1]).startswith("v=1&id=")


# --- LAN addresses --------------------------------------------------------------

IFACES = [("lo", "127.0.0.1"), ("eth0", "192.168.1.5"), ("docker0", "172.17.0.1"),
          ("veth3f2a1b", "172.18.0.1"), ("br-9c0e7d", "172.20.0.1"), ("wlan0", "10.0.0.7"),
          ("eth1", "169.254.10.10"), ("virbr0", "192.168.122.1"), ("hassio", "172.30.32.1"),
          ("eth2", "172.17.5.5"), ("vethb1c2", "172.29.16.1"), ("vEthernet (External)", "192.168.2.9"), ("incusbr0", "10.185.163.1"), ("lxdbr0", "10.9.9.1"),
          ("eth0", "192.168.1.5"), ("eth3", "fe80::1")]


def test_lan_addresses_skip_loopback_docker_and_bridges():
    assert m.announce.lan_addresses(interfaces=IFACES) == ["192.168.1.5", "10.0.0.7", "192.168.2.9"]


def test_speakers_address_comes_first():
    assert m.announce.lan_addresses("10.0.0.7", interfaces=IFACES) == ["10.0.0.7", "192.168.1.5", "192.168.2.9"]
    # a name the speakers are given (not an IP) leads, the addresses follow
    assert m.announce.lan_addresses("nas.lan", interfaces=IFACES)[:2] == ["nas.lan", "192.168.1.5"]


def test_not_on_the_host_network_only_the_speakers_address():
    bridge = [("lo", "127.0.0.1"), ("eth0", "172.18.0.4")]  # Docker bridge: eth0 is the container's
    assert m.announce.lan_addresses("192.168.1.5", interfaces=bridge + [("eth1", "10.9.9.9")]) == ["192.168.1.5"]
    assert m.announce.lan_addresses("192.168.1.5", interfaces=[("lo", "127.0.0.1")]) == ["192.168.1.5"]
    assert m.announce.lan_addresses("", interfaces=[]) == []


def test_addon_asks_the_supervisor_for_the_hosts_addresses(monkeypatch):
    monkeypatch.setattr(m.runtime, "STANDALONE", False)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    seen = {}

    class Response:
        def json(self):
            return {"data": {"interfaces": [
                {"interface": "enp3s0", "enabled": True, "connected": True,
                 "ipv4": {"address": ["192.168.1.20/24"]}},
                {"interface": "wlan0", "enabled": True, "connected": False, "ipv4": {"address": ["10.0.0.3/24"]}},
            ]}}

    def get(url, headers, timeout):
        seen["url"] = url
        return Response()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(get=get))
    assert m.announce.interface_addresses() == [("enp3s0", "192.168.1.20")]
    assert seen["url"] == "http://supervisor/network/info"
    assert m.announce.lan_addresses("192.168.1.20") == ["192.168.1.20"]


# --- sonorium.local and its fallback (zeroconf mocked) -----------------------------

class FakeZeroconf:
    """Answers for names other devices hold; records what Sonorium registers."""
    taken: dict = {}
    instances: list = []

    def __init__(self, interfaces=None):
        self.interfaces = interfaces
        self.registered = []
        self.closed = False
        FakeZeroconf.instances.append(self)

    def register_service(self, info, allow_name_change=False):
        self.registered.append(info)

    def unregister_service(self, info):
        self.registered.remove(info)

    def close(self):
        self.closed = True


class FakeResolver:
    def __init__(self, name):
        self.name = name
        self.answer = []

    def request(self, zc, timeout):
        self.answer = FakeZeroconf.taken.get(self.name, [])
        return bool(self.answer)

    def parsed_addresses(self):
        return list(self.answer)


class FakeServiceInfo:
    def __init__(self, type_, name, *, addresses, port, properties, server):
        self.type, self.name, self.addresses, self.port = type_, name, addresses, port
        self.properties, self.server = properties, server


@pytest.fixture
def zc(monkeypatch):
    fake = types.ModuleType("zeroconf")
    fake.Zeroconf, fake.ServiceInfo, fake.AddressResolverIPv4 = FakeZeroconf, FakeServiceInfo, FakeResolver
    monkeypatch.setitem(sys.modules, "zeroconf", fake)
    FakeZeroconf.taken, FakeZeroconf.instances = {}, []
    return FakeZeroconf


def announce(addresses=("192.168.1.5", "10.0.0.7")):
    a = m.announce.Announcer(addresses=lambda first: list(addresses))
    ok = a.start(server_id="0f8fad5b-d9cb-469f-a165-70867728950e", version="1.6.2", api=1,
                 base_url="http://192.168.1.5:8008", https_port=8808, login_required=True)
    return a, ok


def test_announces_sonorium_local_with_every_address(zc):
    a, ok = announce()
    assert ok and a.hostname == "sonorium.local"
    zc_ = zc.instances[0]
    info = zc_.registered[0]
    assert zc_.interfaces == ["192.168.1.5", "10.0.0.7"]
    assert info.server == "sonorium.local." and len(info.addresses) == 2 and info.port == 8008
    assert info.properties["host"] == "sonorium.local" and info.properties["https"] == "8808"
    a.stop()
    assert zc_.closed and not zc_.registered and a.hostname is None


def test_name_taken_by_another_device_falls_back_to_the_next_free(zc, caplog):
    zc.taken = {"sonorium.local.": ["192.168.1.40"], "sonorium-2.local.": ["192.168.1.41"]}
    with caplog.at_level("INFO"):
        a, ok = announce()
    assert ok and a.hostname == "sonorium-3.local"
    info = zc.instances[0].registered[0]
    assert info.server == "sonorium-3.local." and info.properties["host"] == "sonorium-3.local"
    text = caplog.text
    assert "sonorium.local is taken" in text and "sonorium-2.local is taken" in text
    assert "announced as sonorium-3.local" in text


def test_our_own_answer_is_not_a_conflict(zc):
    zc.taken = {"sonorium.local.": ["10.0.0.7"]}  # an earlier run of this server, still cached
    a, ok = announce()
    assert ok and a.hostname == "sonorium.local"


def test_every_name_taken_still_announces_the_service(zc, caplog):
    zc.taken = {f"{name}.": ["192.168.1.99"] for name in m.announce.hostnames()}
    with caplog.at_level("WARNING"):
        a, ok = announce()
    assert ok and a.hostname is None
    info = zc.instances[0].registered[0]
    assert info.server == "sonorium-0f8fad5b.local." and info.properties["host"] == ""
    assert "are all taken" in caplog.text


def test_no_lan_address_no_announcement(zc):
    a, ok = announce(addresses=())
    assert not ok and a.hostname is None and not zc.instances
