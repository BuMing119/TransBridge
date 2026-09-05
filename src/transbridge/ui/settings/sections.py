"""Stable identifiers for settings-center navigation and deep links."""

from __future__ import annotations

from enum import StrEnum


class SettingsSection(StrEnum):
    APPEARANCE = "appearance"
    AI_SERVICE = "ai_service"
    EMBEDDING = "embedding"
    PARATRANZ = "paratranz"
    AI_DEFAULTS = "ai_defaults"
    TERMINOLOGY = "terminology"
    ADVANCED = "advanced"

    @classmethod
    def parse(cls, value: object) -> SettingsSection:
        if isinstance(value, cls):
            return value
        normalized = str(value or "").strip().casefold().replace("-", "_")
        aliases = {
            "": cls.APPEARANCE,
            "general": cls.APPEARANCE,
            "theme": cls.APPEARANCE,
            "ai": cls.AI_SERVICE,
            "llm": cls.AI_SERVICE,
            "services": cls.AI_SERVICE,
            "semantic": cls.EMBEDDING,
            "paratranz_service": cls.PARATRANZ,
            "defaults": cls.AI_DEFAULTS,
            "terms": cls.TERMINOLOGY,
            "security": cls.ADVANCED,
            "mcp": cls.ADVANCED,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError:
            return cls.APPEARANCE


SECTION_LABELS: tuple[tuple[SettingsSection, str], ...] = (
    (SettingsSection.APPEARANCE, "外观"),
    (SettingsSection.AI_SERVICE, "AI 服务"),
    (SettingsSection.EMBEDDING, "Embedding 与语义检索"),
    (SettingsSection.PARATRANZ, "ParaTranz"),
    (SettingsSection.AI_DEFAULTS, "AI 默认参数"),
    (SettingsSection.TERMINOLOGY, "术语与词典"),
    (SettingsSection.ADVANCED, "高级与安全"),
)


__all__ = ["SECTION_LABELS", "SettingsSection"]
