"""A theme id never reaches outside the audio folder: "..", "." and paths don't match a folder."""
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def test_exact_theme_lookups_are_guarded():
    for rel in ("api.py", "web/api_v2.py"):
        src = (PACKAGE / rel).read_text(encoding="utf-8")
        assert "exact_path = mp / theme_id" in src, rel
        for line_no, line in enumerate(src.splitlines()):
            if "exact_path = mp / theme_id" in line:
                guard = src.splitlines()[line_no - 1]
                assert 'theme_id not in ("", ".", "..")' in guard and '"/" not in theme_id' in guard, (rel, guard)


def test_guard_rejects_parent_and_paths():
    def allowed(theme_id):
        return theme_id not in ("", ".", "..") and "/" not in theme_id and "\\" not in theme_id
    for bad in ("..", ".", "", "../x", "a/b", "a" + chr(92) + "b", ".." + chr(92) + ".."):
        assert not allowed(bad), bad
    for good in ("tavern", "a_rainy_day", "Sleigh Ride", "x.y"):
        assert allowed(good), good
