"""
sonorium.utils.sanitize_id gives exactly what fmtr.tools' string_tools.sanitize
gave (theme folder names -> the dashed IDs older themes are found by).

EXPECTED was recorded from fmtr.tools 1.5.4's own sanitize(), run in a scratch
directory outside the repo; it is not computed by the code under test.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ADDON = Path(__file__).resolve().parent.parent / "sonorium_addon" / "sonorium"

# (input, fmtr.tools sanitize output)
EXPECTED = [
    ('', ''),
    (' ', '-'),
    ('   ', '-'),
    ('-', ''),
    ('_', ''),
    ('-_- _', '-'),
    ('a', 'a'),
    ('A', 'a'),
    ('Rain', 'rain'),
    ('Rain Storm', 'rain-storm'),
    ('Rain  Storm', 'rain-storm'),
    ('Rain\tStorm\nNight', 'rainstormnight'),
    ('  leading', '-leading'),
    ('trailing  ', 'trailing-'),
    ('  both  ', '-both-'),
    ('--leading-dash', 'leadingdash'),
    ('trailing-dash--', 'trailingdash'),
    ('__under__score__', 'underscore'),
    ('Rain & Thunder', 'rain-thunder'),
    ('Rain&Thunder', 'rainthunder'),
    ('Rain - Thunder', 'rain-thunder'),
    ('rain_-_thunder', 'rainthunder'),
    ('Rain.Thunder', 'rainthunder'),
    ('Rain/Thunder', 'rainthunder'),
    ('Rain\\Thunder', 'rainthunder'),
    ('Caf\xe9 Ambience', 'caf\xe9-ambience'),
    ('CAF\xc9', 'caf\xe9'),
    ('\xd1and\xfa', '\xf1and\xfa'),
    ('Stra\xdfe', 'stra\xdfe'),
    ('\u0130stanbul', 'i\u0307stanbul'),
    ('\u03a3\u038a\u03a3\u03a5\u03a6\u039f\u03a3', '\u03c3\u03af\u03c3\u03c5\u03c6\u03bf\u03c3'),
    ("\xd6d\xf6n's Forest", '\xf6d\xf6ns-forest'),
    ('na\xefve fa\xe7ade', 'na\xefve-fa\xe7ade'),
    ('\u6771\u4eac\u306e\u96e8', '\u6771\u4eac\u306e\u96e8'),
    ('\u0414\u043e\u0436\u0434\u044c \u0432 \u043b\u0435\u0441\u0443', '\u0434\u043e\u0436\u0434\u044c-\u0432-\u043b\u0435\u0441\u0443'),
    ('\u0645\u0637\u0631', '\u0645\u0637\u0631'),
    ('\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd', '\u05e9\u05dc\u05d5\u05dd-\u05e2\u05d5\u05dc\u05dd'),
    ('\u0939\u093f\u0928\u094d\u0926\u0940 \u0927\u094d\u0935\u0928\u093f', '\u0939\u0928\u0926-\u0927\u0935\u0928'),
    ('Rain \U0001f327\ufe0f Night', 'rain-night'),
    ('\U0001f525', ''),
    ('\U0001f525 Fire \U0001f525', '-fire-'),
    ('a\u200bb', 'ab'),
    ('a\xa0b', 'ab'),
    ('a\u2003b', 'ab'),
    ('a\u3000b', 'ab'),
    ('\ufeffBOM', 'bom'),
    ('x\xb2', 'x\xb2'),
    ('\xbd Moon', '\xbd-moon'),
    ('\u216b Twelve', '\u217b-twelve'),
    ('\u2460\u2461', '\u2460\u2461'),
    ('\u0663 arabic digit', '\u0663-arabic-digit'),
    ('1984', '1984'),
    ('007 Agent', '007-agent'),
    ('v1.2.3', 'v123'),
    ('Ocean (Night)', 'ocean-night'),
    ('Ocean [Remix]', 'ocean-remix'),
    ('Ocean {x}', 'ocean-x'),
    ("It's Raining", 'its-raining'),
    ('"Quoted"', 'quoted'),
    ('Rain, Wind; Fire!', 'rain-wind-fire'),
    ('50% Rain', '50-rain'),
    ('a+b=c', 'abc'),
    ('#hash', 'hash'),
    ('@at', 'at'),
    ('$money', 'money'),
    ('~tilde`', 'tilde'),
    ('Tab\t\tTabs', 'tabtabs'),
    ('Line\r\nBreak', 'linebreak'),
    ('a\x00b', 'ab'),
    ('a\x1fb', 'ab'),
    ('Mixed-_ -Separators', 'mixed-separators'),
    ('a--b__c  d', 'abc-d'),
    (' - ', '-'),
    ('Theme_01', 'theme01'),
    ('theme-01', 'theme01'),
    ('THEME 01', 'theme-01'),
    ('\ufb01ne ligature', '\ufb01ne-ligature'),
    ('\xc5', '\xe5'),
    ('A\u030a', 'a'),
    ('e\u0301', 'e'),
    ('\u01c5 titlecase', '\u01c6-titlecase'),
    ('\xdf', '\xdf'),
    ('\ufb00', '\ufb00'),
    ('\u24b6 circled', '-circled'),
    ('\U0001d509\U0001d52f\U0001d51e\U0001d528\U0001d531\U0001d532\U0001d52f', '\U0001d509\U0001d52f\U0001d51e\U0001d528\U0001d531\U0001d532\U0001d52f'),
    ('\uff46\uff55\uff4c\uff4c', '\uff46\uff55\uff4c\uff4c'),
    ('\u3000', ''),
    ('\u2028sep', 'sep'),
    ('a\x85b', 'ab'),
    ('Example Theme', 'example-theme'),
    ('Thunderstorm & Rain (Heavy) - v2', 'thunderstorm-rain-heavy-v2'),
    ('Forest_Birds - Morning', 'forestbirds-morning'),
    ('Campfire...', 'campfire'),
    ('...', ''),
    ('\u1e9e capital sharp s', '\xdf-capital-sharp-s'),
    ('\u0130', 'i\u0307'),
    ('K\u212a kelvin', 'kk-kelvin'),
    ('\u2126 ohm', '\u03c9-ohm'),
    ('\xb5 micro', '\xb5-micro'),
    ('a\u0300\u0301b', 'ab'),
]


@pytest.fixture(scope="module")
def sanitize_id():
    """utils.py on its own (stub logger), so the test needs nothing else of Sonorium."""
    obs = types.ModuleType("sonorium.obs")
    obs.logger = types.SimpleNamespace(info=print, warning=print, error=print, debug=print)
    saved = {k: sys.modules.get(k) for k in ("sonorium", "sonorium.obs")}
    sys.modules.setdefault("sonorium", types.ModuleType("sonorium"))
    sys.modules["sonorium.obs"] = obs
    try:
        spec = importlib.util.spec_from_file_location("_sonorium_utils_under_test", ADDON / "utils.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return module.sanitize_id


@pytest.mark.parametrize("text,expected", EXPECTED)
def test_matches_fmtr_sanitize(sanitize_id, text, expected):
    assert sanitize_id(text) == expected


def test_joins_several_strings_like_fmtr(sanitize_id):
    # fmtr joined its arguments with a space, skipping empty ones
    assert sanitize_id("Rain", "", "Storm") == "rain-storm"
    assert sanitize_id("Rain Storm", sep="_") == "rain_storm"
