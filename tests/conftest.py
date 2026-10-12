"""Keep manual LAN diagnostics out of automated test collection."""

import pytest


collect_ignore = [
    "test_airplay_airconnect.py",
    "test_airplay_streaming.py",
    "test_arylic_http.py",
    "test_rtsp_diagnostic.py",
]
