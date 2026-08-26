from dataclasses import dataclass, field
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from transbridge.config.paratranz_credentials import (
    CredentialRef,
    SecretStoreCapability,
    SecretValue,
)
from transbridge.config.repository import ConfigRepository


@dataclass
class _MemoryCredentialStore:
    values: dict[str, SecretValue] = field(default_factory=dict)

    @property
    def capability(self) -> SecretStoreCapability:
        return SecretStoreCapability(True, True)

    def get(self, reference: CredentialRef) -> SecretValue | None:
        return self.values.get(reference.target_name)

    def set(self, reference: CredentialRef, value: SecretValue) -> None:
        self.values[reference.target_name] = value

    def delete(self, reference: CredentialRef) -> None:
        self.values.pop(reference.target_name, None)


@pytest.fixture(autouse=True)
def _isolate_ai_tool_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep UI autosave tests away from the user's real config and credential store."""

    from transbridge.config import llm as llm_module

    store = _MemoryCredentialStore()
    repository = ConfigRepository(
        tmp_path / "transbridge.ini",
        legacy_path=tmp_path / "legacy.ini",
        credential_store=store,
    )
    monkeypatch.setattr(llm_module, "default_credential_store", lambda: store)
    monkeypatch.setattr(llm_module, "default_config_repository", lambda **_kwargs: repository)
