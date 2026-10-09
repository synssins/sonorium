"""Cast displays show the Sonorium logo, served from the same address as the stream."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "display_under_test", Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "display.py")
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)


def test_logo_url_from_stream_url():
    assert display.display_image_url("http://192.168.1.20:8008/stream/channel1") == "http://192.168.1.20:8008/display.png"


def test_no_logo_for_unexpected_url():
    assert display.display_image_url("http://host/other") is None
    assert display.display_image_url("") is None
