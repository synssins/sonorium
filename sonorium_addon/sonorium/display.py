"""
What speakers with a screen (Google Cast displays such as the Nest Hub) show
while Sonorium plays. Static on purpose: changing it would mean restarting the
stream on the device, so it isn't updated when a channel changes theme.
"""
from typing import Optional

DISPLAY_TITLE = "Sonorium"


def display_image_url(stream_url: str) -> Optional[str]:
    """
    The large Sonorium logo, served by Sonorium at the same address the
    speaker streams from (so a speaker that can play the stream can load it).
    """
    base, separator, _ = (stream_url or "").partition("/stream/")
    return f"{base}/logo.png" if separator else None
