"""Detached task configuration for the batch AI dialog."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from transbridge.config.llm import LLMConfig


@dataclass(slots=True)
class BatchTranslationDraft:
    """Own per-run overrides without mutating the saved global configuration."""

    config: LLMConfig
    overwrite: bool = False

    @classmethod
    def from_config(cls, config: LLMConfig) -> BatchTranslationDraft:
        copier = getattr(config, "copy_for_execution", None)
        detached = copier() if callable(copier) else deepcopy(config)
        return cls(config=detached)

    def execution_config(self) -> LLMConfig:
        """Return another detached value for the runtime hand-off."""

        copier = getattr(self.config, "copy_for_execution", None)
        return copier() if callable(copier) else deepcopy(self.config)

    def refresh_service_from(self, config: LLMConfig) -> None:
        """Refresh only global service identity after the settings dialog closes."""

        self.config.provider = config.provider
        self.config.api_key = config.api_key
        self.config.base_url = config.base_url
        self.config.model = config.model
        self.config.config_revision = config.config_revision


__all__ = ["BatchTranslationDraft"]
