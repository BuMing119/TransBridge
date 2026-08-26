from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import (
    CredentialRef,
    CredentialStorageError,
    SecretStoreCapability,
    SecretValue,
)
from transbridge.config.repository import (
    ConfigFutureSchemaError,
    ConfigMigrationError,
    ConfigRepository,
    ConfigRepositoryError,
)


@dataclass
class MemoryStore:
    fail_set: bool = False
    values: dict[str, SecretValue] = field(default_factory=dict)

    @property
    def capability(self) -> SecretStoreCapability:
        return SecretStoreCapability(True, True)

    def get(self, reference: CredentialRef) -> SecretValue | None:
        return self.values.get(reference.target_name)

    def set(self, reference: CredentialRef, value: SecretValue) -> None:
        if self.fail_set:
            raise CredentialStorageError("injected")
        self.values[reference.target_name] = value

    def delete(self, reference: CredentialRef) -> None:
        self.values.pop(reference.target_name, None)


def _repository(tmp_path: Path, store: MemoryStore | None = None) -> ConfigRepository:
    return ConfigRepository(
        tmp_path / "transbridge.ini",
        legacy_path=tmp_path / "paratranz_config.ini",
        credential_store=store or MemoryStore(),
    )


def test_term_csv_source_persists_and_old_default_priority_migrates(tmp_path: Path) -> None:
    store = MemoryStore()
    repository = _repository(tmp_path, store)
    repository.update_sections({
        "llm": {
            "provider": "openai",
            "base_url": "https://example.test/v1",
            "model": "m1",
            "term_priority": "dynamic,paratranz,json,excel",
        }
    })

    config = LLMConfig.load_from_file(repository=repository, credential_store=store, environment={})
    assert config.term_priority == ["dynamic", "paratranz", "json", "csv", "excel"]

    config.local_csv_path = "terms.csv"
    config.save_to_file(repository=repository, credential_store=store)
    reloaded = LLMConfig.load_from_file(repository=repository, credential_store=store, environment={})

    assert reloaded.local_csv_path == "terms.csv"
    assert reloaded.term_priority == ["dynamic", "paratranz", "json", "csv", "excel"]


def test_endpoint_identity_is_atomic_and_revisioned(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.update_sections({
        "llm": {"provider": "openai", "base_url": "https://example.test/v1", "model": "m1"}
    })

    with pytest.raises(ConfigRepositoryError, match="updated atomically"):
        repository.update_sections({"llm": {"model": "m2"}})

    second = repository.update_sections({"guardrails": {"max_input_size": 42}})
    assert first.revision == 1
    assert second.revision == 2
    assert second.value("llm", "model") == "m1"


def test_concurrent_section_updates_do_not_lose_data(tmp_path: Path) -> None:
    path = tmp_path / "transbridge.ini"

    def update(index: int) -> int:
        snapshot = ConfigRepository(path, legacy_path=path, credential_store=MemoryStore()).update_sections({
            f"worker.{index}": {"value": index}
        })
        return snapshot.revision

    with ThreadPoolExecutor(max_workers=6) as executor:
        revisions = list(executor.map(update, range(12)))

    snapshot = ConfigRepository(path, legacy_path=path, credential_store=MemoryStore()).load()
    assert sorted(revisions) == list(range(1, 13))
    assert snapshot.revision == 12
    assert all(snapshot.value(f"worker.{index}", "value") == str(index) for index in range(12))


def test_replace_fault_preserves_last_verified_file(tmp_path: Path) -> None:
    path = tmp_path / "transbridge.ini"
    repository = ConfigRepository(path, legacy_path=path, credential_store=MemoryStore())
    repository.update_sections({"guardrails": {"max_input_size": 10}})
    before = path.read_bytes()

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        del source, target
        raise OSError("injected replace failure")

    faulty = ConfigRepository(
        path,
        legacy_path=path,
        credential_store=MemoryStore(),
        replace_func=fail_replace,
    )
    with pytest.raises(OSError, match="injected"):
        faulty.update_sections({"guardrails": {"max_input_size": 11}})
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing race contract")
def test_atomic_write_retries_transient_windows_sharing_violation(tmp_path: Path) -> None:
    path = tmp_path / "transbridge.ini"
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "transient sharing violation")
        real_replace(source, target)

    repository = ConfigRepository(
        path,
        legacy_path=path,
        credential_store=MemoryStore(),
        replace_func=flaky_replace,
    )
    snapshot = repository.update_sections({"guardrails": {"max_input_size": 10}})

    assert attempts == 3
    assert snapshot.revision == 1
    assert ConfigRepository(path, legacy_path=path, credential_store=MemoryStore()).load().revision == 1


def test_legacy_migration_is_sanitized_verified_and_drops_profiles(tmp_path: Path) -> None:
    legacy = tmp_path / "paratranz_config.ini"
    canaries = {
        "pt": "pt-secret-canary",
        "llm": "llm-secret-canary",
        "embedding": "embedding-secret-canary",
        "mcp": "mcp-secret-canary",
    }
    legacy.write_text(
        "[api]\n"
        f"token = {canaries['pt']}\n"
        "base_url = https://paratranz.cn/api\n"
        "[llm]\n"
        f"api_key = {canaries['llm']}\n"
        f"embedding_api_key = {canaries['embedding']}\n"
        "provider = openai\nbase_url = https://example.test/v1\nmodel = m1\n"
        "[mcp]\n"
        f"auth_token = {canaries['mcp']}\n"
        "[llm_profiles]\nold = https://obsolete.invalid\n",
        encoding="utf-8",
    )
    store = MemoryStore()
    repository = _repository(tmp_path, store)

    snapshot = repository.load()

    rendered = (tmp_path / "transbridge.ini").read_text(encoding="utf-8")
    backup = (tmp_path / "paratranz_config.ini.validated.bak").read_text(encoding="utf-8")
    assert snapshot.revision == 1
    assert snapshot.section("llm_profiles").values == ()
    assert not legacy.exists()
    assert all(secret not in rendered and secret not in backup for secret in canaries.values())
    assert len(store.values) == 4


def test_secret_migration_failure_leaves_legacy_source_untouched(tmp_path: Path) -> None:
    legacy = tmp_path / "paratranz_config.ini"
    legacy.write_text("[api]\ntoken = keep-secret\n", encoding="utf-8")
    original = legacy.read_bytes()

    with pytest.raises(ConfigMigrationError, match="could not be verified"):
        _repository(tmp_path, MemoryStore(fail_set=True)).load()

    assert legacy.read_bytes() == original
    assert not (tmp_path / "transbridge.ini").exists()


def test_future_schema_and_plaintext_secret_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "transbridge.ini"
    path.write_text("[meta]\nschema_version = 999\nrevision = 1\n", encoding="utf-8")
    repository = ConfigRepository(path, legacy_path=path, credential_store=MemoryStore())
    with pytest.raises(ConfigFutureSchemaError):
        repository.load()
    with pytest.raises(ConfigRepositoryError, match="plaintext secret"):
        repository.update_sections({"llm": {"api_key": "never"}})


def test_llm_and_paratranz_facades_observe_same_revision(tmp_path: Path) -> None:
    store = MemoryStore()
    repository = _repository(tmp_path, store)
    expected = repository.update_sections({
        "llm": {
            "provider": "openai",
            "base_url": "https://example.test/v1",
            "model": "m1",
            "target_lang": "zh_CN",
        },
        "paratranz": {
            "base_url": "https://paratranz.cn/api",
            "timeout": 30,
            "credential_ref": "TransBridge.ParaTranz:default",
        },
    })

    llm = LLMConfig.load_from_file(repository=repository, credential_store=store, environment={})
    paratranz = ParatranzConfig.load_from_file(
        repository=repository,
        credential_store=store,
        environment={},
        config_path=repository.path,
    )

    assert llm.config_revision == expected.revision == paratranz.config_revision
    assert llm.provider == "openai"
    assert paratranz.base_url == "https://paratranz.cn/api"


def test_empty_action_rules_remove_stale_value(tmp_path: Path) -> None:
    store = MemoryStore()
    repository = _repository(tmp_path, store)
    config = LLMConfig(model="m1")
    config.action_rules = [{"rule_id": "one", "action": "skip"}]
    config.save_to_file(repository=repository, credential_store=store)
    config.action_rules = []
    config.save_to_file(repository=repository, credential_store=store)
    assert repository.load().value("llm", "action_rules") is None


def test_environment_llm_secret_is_read_only_and_not_persisted(tmp_path: Path) -> None:
    store = MemoryStore()
    repository = _repository(tmp_path, store)
    repository.update_sections({
        "llm": {
            "provider": "openai",
            "base_url": "https://example.test/v1",
            "model": "m1",
            "credential_ref": "TransBridge.LLM:default",
        }
    })
    canary = "environment-only-llm-secret"
    config = LLMConfig.load_from_file(
        repository=repository,
        credential_store=store,
        environment={"TRANSBRIDGE_LLM_API_KEY": canary},
    )

    config.target_lang = "ja_JP"
    config.save_to_file(repository=repository, credential_store=store)

    assert config.api_key == canary
    assert not store.values
    assert canary not in repository.path.read_text(encoding="utf-8")


def test_gui_agent_mcp_and_fomod_observe_one_config_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from transbridge.smart_assistant.tools import _common
    from transbridge.smart_assistant.tools.tool_translator import TranslationController

    store = MemoryStore()
    repository = _repository(tmp_path, store)
    expected = repository.update_sections({
        "llm": {
            "provider": "openai",
            "base_url": "https://example.test/v1",
            "model": "m1",
        }
    })
    # GUI and FOMOD both consume the compatibility facade; Agent and MCP share
    # the registered translator controller and its common loader.
    gui_config = LLMConfig.load_from_file(repository=repository, credential_store=store)
    fomod_config = LLMConfig.load_from_file(repository=repository, credential_store=store)
    monkeypatch.setattr(_common, "load_llm_config", lambda: gui_config)
    tool_result = TranslationController().get_translation_config({}, SimpleNamespace(esp_path=""))

    assert tool_result.success
    assert {
        gui_config.config_revision,
        fomod_config.config_revision,
        tool_result.data["config_revision"],
    } == {expected.revision}


def test_agent_endpoint_update_rejects_partial_identity_and_commits_one_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from transbridge.smart_assistant.tools import _common
    from transbridge.smart_assistant.tools.tool_translator import TranslationController

    store = MemoryStore()
    repository = _repository(tmp_path, store)
    repository.update_sections({
        "llm": {
            "provider": "old",
            "base_url": "https://old.example/v1",
            "model": "old-model",
        }
    })
    config = LLMConfig.load_from_file(repository=repository, credential_store=store)
    monkeypatch.setattr(_common, "load_llm_config", lambda: config)
    controller = TranslationController()

    rejected = controller.set_translation_config({"model": "partial"}, None)
    accepted = controller.set_translation_config(
        {
            "provider": "new",
            "base_url": "https://new.example/v1",
            "model": "new-model",
            "target_lang": "ja_JP",
        },
        None,
    )
    snapshot = repository.load()

    assert not rejected.success
    assert accepted.success
    assert accepted.data["config_revision"] == snapshot.revision == 2
    assert (
        snapshot.value("llm", "provider"),
        snapshot.value("llm", "base_url"),
        snapshot.value("llm", "model"),
        snapshot.value("llm", "target_lang"),
    ) == ("new", "https://new.example/v1", "new-model", "ja_JP")
