"""Theme folders are made, found and imported in the configured audio folder, never a hard-coded one."""
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def test_no_hard_coded_media_paths():
    offenders = []
    for py in PACKAGE.rglob("*.py"):
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if '"/media/sonorium"' in code or '"/share/sonorium"' in code:
                offenders.append(f"{py.relative_to(PACKAGE)}:{n}")
    assert not offenders, offenders


def test_audio_dir_reads_the_configured_folder():
    source = (PACKAGE / "paths.py").read_text(encoding="utf-8")
    assert "def audio_dir()" in source
    assert "settings.path_audio" in source
    for rel in ("plugins/base.py", "plugins/manager.py", "plugins/loader.py"):
        assert "audio_path or audio_dir()" in (PACKAGE / rel).read_text(encoding="utf-8"), rel
