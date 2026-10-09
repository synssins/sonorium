"""Keep manual LAN diagnostics out of automated test collection."""

import pytest


collect_ignore = [
    "test_airplay_airconnect.py",
    "test_airplay_streaming.py",
    "test_arylic_http.py",
    "test_rtsp_diagnostic.py",
]


def pytest_collection_modifyitems(items):
    for item in items:
        if item.nodeid.endswith("test_linkplay_integration.py::test_linkplay_streaming"):
            item.add_marker(pytest.mark.skip(reason="Requires a live LAN speaker; run the script manually"))