from __future__ import annotations

import configparser
from dataclasses import dataclass
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import (
    CredentialRef,
    CredentialStorageError,
    SecretStoreCapability,
    SecretValue,
    redact_credential_data,
)
from transbridge.config.repository import ConfigMigrationError, ConfigRepository
from transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI
from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from transbridge.paratranz.paratranz_client import (
    ParatranzClient,
    ParatranzCredentialRequiredError,
)


@dataclass
class MemoryStore:
    writable: bool = True
    fail_set: bool = False
    fail_delete: bool = False
    value: SecretValue | None = None
    get_calls: int = 0
    set_calls: int = 0

    @property
    def capability(self) -> SecretStoreCapability:
        return SecretStoreCapability(True, self.writable)

    def get(self, reference: CredentialRef) -> SecretValue | None:
        del reference
        self.get_calls += 1
        return self.value

    def set(self, reference: CredentialRef, value: SecretValue) -> None:
        del reference
        self.set_calls += 1
        if self.fail_set:
            raise CredentialStorageError("injected secure-store failure")
        self.value = value

    def delete(self, reference: CredentialRef) -> None:
        del reference
        if self.fail_delete:
            raise CredentialStorageError("injected secure-store delete failure")
        self.value = None


def _legacy_ini(path: Path, token: str) -> bytes:
    content = f"[api]\nbase_url = https://paratranz.cn/api\ntimeout = 15\ntoken = {token}\n\n[llm]\nmodel = keep-me\n"
    path.write_text(content, encoding="utf-8")
    return path.read_bytes()


def test_secret_value_and_shared_redactor_cover_all_canary_shapes() -> None:
    canary = "pt-canary-value-0123456789"
    secret = SecretValue(canary)
    payload = {
        "plain": canary,
        "authorization": f"Bearer {canary}",
        "url": f"https://example.invalid/sync?token={canary}",
        "nested": [{"detail": f"failure included {canary}"}],
    }

    projected = redact_credential_data(payload, secret)
    rendered = repr(projected)

    assert canary not in rendered
    assert canary not in repr(secret)
    assert canary not in str(secret)
    assert rendered.count("***REDACTED***") >= 4

    config = ParatranzConfig(
        token=canary,
        base_url=f"https://example.invalid/?credential={canary}",
        credential_store=MemoryStore(),
        environment={},
    )
    assert canary not in repr(config)


def test_save_stores_secret_then_atomically_writes_only_reference(tmp_path: Path) -> None:
    path = tmp_path / "paratranz_config.ini"
    path.write_text("[llm]\nmodel = keep-me\n", encoding="utf-8")
    store = MemoryStore()
    config = ParatranzConfig(
        token="pt-save-canary-0123456789",
        timeout=22,
        credential_store=store,
        environment={},
        config_path=path,
    )

    config.save_to_file()

    saved = path.read_text(encoding="utf-8")
    assert "pt-save-canary" not in saved
    assert "credential_ref = TransBridge.ParaTranz:default" in saved
    assert "model = keep-me" in saved
    assert store.set_calls == 1


def test_legacy_ini_migration_is_verified_and_preserves_other_sections(tmp_path: Path) -> None:
    path = tmp_path / "paratranz_config.ini"
    canary = "pt-legacy-canary-0123456789"
    _legacy_ini(path, canary)
    store = MemoryStore()

    config = ParatranzConfig.load_from_file(credential_store=store, environment={}, config_path=path)

    assert config.token == canary
    assert not config.credential_capability.degraded
    saved = path.read_text(encoding="utf-8")
    assert canary not in saved
    assert "credential_ref = TransBridge.ParaTranz:default" in saved
    assert "model = keep-me" in saved


@pytest.mark.parametrize("failure", ["store", "replace"])
def test_legacy_migration_failure_keeps_source_and_fails_closed(tmp_path: Path, failure: str) -> None:
    path = tmp_path / "paratranz_config.ini"
    canary = "pt-failed-migration-0123456789"
    original = _legacy_ini(path, canary)
    store = MemoryStore(fail_set=failure == "store")
    repository = ConfigRepository(
        path,
        legacy_path=path,
        credential_store=store,
        replace_func=((lambda *_: (_ for _ in ()).throw(OSError("injected"))) if failure == "replace" else os.replace),
    )
    expected_error = OSError if failure == "replace" else ConfigMigrationError
    with pytest.raises(expected_error):
        ParatranzConfig.load_from_file(
            credential_store=store,
            environment={},
            config_path=path,
            repository=repository,
        )

    assert path.read_bytes() == original


def test_environment_override_has_priority_and_is_never_written(tmp_path: Path) -> None:
    path = tmp_path / "paratranz_config.ini"
    parser = configparser.ConfigParser()
    parser["api"] = {"credential_ref": "TransBridge.ParaTranz:default"}
    with path.open("w", encoding="utf-8") as stream:
        parser.write(stream)
    store = MemoryStore(value=SecretValue("pt-store-canary-0123456789"))
    env_token = "pt-env-canary-0123456789"

    config = ParatranzConfig.load_from_file(
        credential_store=store,
        environment={"TRANSBRIDGE_PARATRANZ_TOKEN": env_token},
        config_path=path,
    )
    config.save_to_file()

    assert config.token == env_token
    assert store.set_calls == 0
    assert env_token not in path.read_text(encoding="utf-8")


def test_delete_credential_and_failure_are_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "paratranz_config.ini"
    store = MemoryStore(value=SecretValue("pt-delete-canary-0123456789"))
    config = ParatranzConfig(credential_store=store, environment={}, config_path=path)

    config.delete_token()

    assert config.token is None
    assert store.value is None

    failing = MemoryStore(value=SecretValue("pt-delete-failure-0123456789"), fail_delete=True)
    config = ParatranzConfig(credential_store=failing, environment={}, config_path=path)
    with pytest.raises(CredentialStorageError, match="secure credential delete failed"):
        config.delete_token()
    assert config.credential_capability.degraded
    assert config.credential_capability.reason_code == "SECURE_STORE_DELETE_FAILED"


def test_missing_credential_has_no_authorization_header(tmp_path: Path) -> None:
    config = ParatranzConfig(credential_store=MemoryStore(), environment={}, config_path=tmp_path / "config.ini")

    assert config.token is None
    assert "Authorization" not in config.get_headers()

    client = ParatranzClient(config=config)
    with pytest.raises(ParatranzCredentialRequiredError) as captured:
        client._request("GET", "/projects")
    assert captured.value.error_code == "PARATRANZ_CREDENTIAL_REQUIRED"


def test_empty_and_legacy_context_config_construct_as_missing_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRANSBRIDGE_PARATRANZ_TOKEN", raising=False)
    empty = ParatranzConfig(
        token="",
        credential_store=MemoryStore(),
        environment={},
        config_path=tmp_path / "config.ini",
    )
    assert empty.token is None
    assert "Authorization" not in empty.get_headers()

    legacy = MagicMock()
    legacy.token = ""
    legacy.api_token = ""
    client = ParatranzClient(legacy)
    assert client.config.token is None
    with pytest.raises(ParatranzCredentialRequiredError):
        client._request("GET", "/projects")


def test_client_redacts_401_body_url_nested_canary_and_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "pt-client-canary-0123456789"
    config = ParatranzConfig(
        token=canary,
        credential_store=MemoryStore(),
        environment={},
        config_path=tmp_path / "config.ini",
    )
    client = ParatranzClient(config=config)
    response = MagicMock()
    response.status_code = 401
    response.ok = False
    response.text = (
        f'{{"nested":{{"token":"{canary}"}},'
        f'"url":"https://example.invalid/?token={canary}",'
        f'"authorization":"Bearer {canary}"}}'
    )
    client._session.request = MagicMock(return_value=response)

    with pytest.raises(RuntimeError) as captured:
        client._request("GET", f"/projects?credential={canary}")

    rendered = str(captured.value)
    streams = capsys.readouterr()
    assert canary not in rendered
    assert canary not in streams.out
    assert canary not in streams.err
    assert "***REDACTED***" in rendered


def test_client_redacts_transport_exception_canary(tmp_path: Path) -> None:
    canary = "pt-transport-canary-0123456789"
    config = ParatranzConfig(
        token=canary,
        credential_store=MemoryStore(),
        environment={},
        config_path=tmp_path / "config.ini",
    )
    client = ParatranzClient(config=config)
    client._session.request = MagicMock(side_effect=requests.ConnectionError(f"nested payload carried {canary}"))

    with pytest.raises(RuntimeError) as captured:
        client._request("POST", "/projects", json={"nested": {"token": canary}})

    assert canary not in str(captured.value)
    assert "***REDACTED***" in str(captured.value)


def test_direct_download_and_translation_paths_share_prerequisite(
    tmp_path: Path,
) -> None:
    config = ParatranzConfig(credential_store=MemoryStore(), environment={}, config_path=tmp_path / "config.ini")
    export_api = ParatranzExportAPI(config=config)
    files_api = ParatranzFilesAPI(config=config)
    network = MagicMock()
    export_api._session.request = network
    files_api._session.request = network
    upload = tmp_path / "upload.json"
    upload.write_text("[]", encoding="utf-8")

    with pytest.raises(ParatranzCredentialRequiredError):
        export_api.download_artifacts(1, str(tmp_path / "artifact.zip"))
    with pytest.raises(ParatranzCredentialRequiredError):
        files_api.update_file_translation(1, 2, str(upload))
    network.assert_not_called()


@pytest.mark.parametrize("api_kind", ["export", "translation"])
def test_direct_request_paths_redact_response_canary(
    tmp_path: Path,
    api_kind: str,
) -> None:
    canary = "pt-direct-canary-0123456789"
    config = ParatranzConfig(
        token=canary,
        credential_store=MemoryStore(),
        environment={},
        config_path=tmp_path / "config.ini",
    )
    response = MagicMock(
        ok=False,
        status_code=401,
        text=f"Bearer {canary}",
        headers={},
    )
    if api_kind == "export":
        api = ParatranzExportAPI(config=config)
        api._session.request = MagicMock(return_value=response)

        def call() -> str:
            return api.download_artifacts(1, str(tmp_path / "artifact.zip"))

    else:
        api = ParatranzFilesAPI(config=config)
        api._session.request = MagicMock(return_value=response)
        upload = tmp_path / "upload.json"
        upload.write_text("[]", encoding="utf-8")

        def call():
            return api.update_file_translation(1, 2, str(upload))

    with pytest.raises(RuntimeError) as captured:
        call()
    assert canary not in str(captured.value)
    assert "***REDACTED***" in str(captured.value)


def test_offline_paratranz_adapter_has_no_network_or_credential_dependency() -> None:
    source = Path("src/transbridge/application/io/paratranz.py").read_text(encoding="utf-8")

    assert "paratranz_client" not in source.casefold()
    assert "paratranz_credentials" not in source.casefold()
    assert "SecretPort" not in source
