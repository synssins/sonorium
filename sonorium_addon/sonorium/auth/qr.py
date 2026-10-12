"""
The Link App QR code as SVG: error correction H, with the logo's sound icon
on a dark rounded tile with a white outline in the middle (about 6% of the
code's area, well inside what level H can lose and still scan).
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode

import segno

LOGO = Path(__file__).resolve().parent.parent / "web" / "static" / "img" / "qr-logo.png"  # logo.png cropped (0, 28, 228, 162)
LOGO_SIZE = (228, 134)
BORDER = 3
DARK = "#0b0f1a"


LINK_URL = "https://sonorium.app/link"
LEGACY_PREFIX = "sonorium://pair?"


def pair_uri(*, server_id: str, name: str, hosts: list[str], http_port: int, https_port: int,
             fingerprint: str, code: str, username: str | None = None) -> str:
    """
    https://sonorium.app/link#v=1&id=..&n=..&h=host[,host2]&p=..&s=..&fp=..&c=..[&u=..]
    (u: the signed-in user, if any). Everything is after the #, so none of it
    reaches the website: a phone without the app lands on a page that says how
    to get it, and the app opens the link itself.
    """
    params = [("v", "1"), ("id", server_id), ("n", name), ("h", ",".join(hosts)), ("p", str(http_port))]
    if https_port:
        params.append(("s", str(https_port)))
    params += [("fp", fingerprint), ("c", code)]
    if username:
        params.append(("u", username))
    return f"{LINK_URL}#" + urlencode(params, quote_via=quote, safe=",")


def parse_link(uri: str) -> dict[str, str]:
    """
    The parameters of a Link App link: the web address above, or the older
    sonorium://pair?v=1&... (same parameters after the ?), which the app still
    accepts. {} when it's neither.
    """
    uri = (uri or "").strip()
    if uri.startswith(LINK_URL + "#") or uri.startswith(LINK_URL + "/#"):
        query = uri.split("#", 1)[1]
    elif uri.startswith(LEGACY_PREFIX):
        query = uri[len(LEGACY_PREFIX):]
    else:
        return {}
    return dict(parse_qsl(query, keep_blank_values=True))


@lru_cache(maxsize=1)
def _logo_data() -> str:
    try:
        return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    except OSError:
        return ""


def qr_svg(data: str) -> str:
    qr = segno.make(data, error="h", micro=False)
    rows = qr.matrix
    n = len(rows) + 2 * BORDER
    parts = []
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                parts.append(f"M{start + BORDER} {y + BORDER}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    tw = n * 0.30
    th = tw * 0.62
    tx, ty = (n - tw) / 2, (n - th) / 2
    stroke = max(1 / 3, n / 90)
    lw = tw * 0.86
    lh = lw * LOGO_SIZE[1] / LOGO_SIZE[0]
    logo = _logo_data()
    image = (f'<image href="{logo}" x="{(n - lw) / 2:.3f}" y="{(n - lh) / 2:.3f}" width="{lw:.3f}" height="{lh:.3f}"/>'
             if logo else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path fill="{DARK}" d="{"".join(parts)}"/>'
            f'<rect x="{tx + stroke / 2:.3f}" y="{ty + stroke / 2:.3f}" width="{tw - stroke:.3f}" height="{th - stroke:.3f}" '
            f'rx="{th * 0.22:.3f}" fill="#141828" stroke="#fff" stroke-width="{stroke:.3f}" shape-rendering="geometricPrecision"/>'
            f'{image}</svg>')
