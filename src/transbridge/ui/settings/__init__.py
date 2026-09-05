"""Composable pages and detached drafts for the unified settings dialog."""

from .draft import SettingsConfigDraft, SettingsSaveResult
from .sections import SettingsSection

__all__ = ["SettingsConfigDraft", "SettingsSaveResult", "SettingsSection"]
