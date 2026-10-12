"""The dashboard cards' JavaScript parses, registers its elements and renders (tests/ha_cards_check.mjs, needs node)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node isn't installed")
def test_cards_render_headless():
    result = subprocess.run([NODE, str(ROOT / "tests" / "ha_cards_check.mjs")], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "sonorium-cards.js: ok" in result.stdout
