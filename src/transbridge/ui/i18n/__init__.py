"""Static gettext resource location and source-locale metadata."""

from __future__ import annotations

from pathlib import Path

CATALOG_DOMAIN = "transbridge"
CATALOG_SCHEMA_VERSION = 1
SOURCE_LOCALE = "zh-CN"


def catalog_root() -> Path:
    """Return the onedir/PyInstaller-discoverable gettext locale directory."""

    return Path(__file__).resolve().parent / "locales"


def source_template() -> Path:
    return Path(__file__).resolve().parent / "messages.pot"


__all__ = [
    "CATALOG_DOMAIN",
    "CATALOG_SCHEMA_VERSION",
    "SOURCE_LOCALE",
    "catalog_root",
    "source_template",
]
