"""A theme zip can't write outside its theme folder: '..', absolute paths and drive letters are refused."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "web" / "api_v2.py").read_text(encoding="utf-8")


def test_import_refuses_unsafe_entries_before_writing():
    assert "Zip entry not allowed" in SRC
    assert "'..' in entry_parts" in SRC
    assert "is_relative_to(target_path.resolve())" in SRC
    assert "Zip has no usable theme folder name" in SRC


def _refused(entry):
    entry_parts = entry.replace(chr(92), '/').split('/')
    return entry.startswith(('/', chr(92))) or ':' in entry_parts[0] or '..' in entry_parts


def test_entry_rule():
    for bad in ("../evil.py", "Theme/../../evil.py", "/etc/passwd", chr(92) + "x", "C:/x", "a" + chr(92) + ".." + chr(92) + "b"):
        assert _refused(bad), bad
    for good in ("Theme/Rain.mp3", "Theme/Wind/Gust.wav", "metadata.json", "Theme/a..b.mp3"):
        assert not _refused(good), good
