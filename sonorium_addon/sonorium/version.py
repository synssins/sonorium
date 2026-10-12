"""Sonorium's version, kept in the plain-text `version` file beside this module (the release workflow writes it)."""
from pathlib import Path

__version__ = Path(__file__).resolve().with_name("version").read_text(encoding="utf-8").strip()
