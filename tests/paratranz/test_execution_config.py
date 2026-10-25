"""Execution settings are pinned values, independent of live credential providers."""

import pytest

from tests.paratranz.test_credentials_security import MemoryStore
from transbridge.config import paratranz
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import CredentialRef, SecretValue


@pytest.mark.parametrize("source", ["memory", "environment", "store", "missing"])
def test_execution_copy_pins_settings_without_reading_live_services(tmp_path, monkeypatch, source):
    token = "execution-test-secret"
    store = MemoryStore(value=SecretValue(token) if source == "store" else None)
    environment = {"TRANSBRIDGE_PARATRANZ_TOKEN": token} if source == "environment" else {}
    config = ParatranzConfig(
        token=token if source == "memory" else None,
        user_id=41,
        timeout=23,
        base_url="https://example.invalid/api",
        extra_headers={"X-Project": "captured"},
        credential_ref=CredentialRef("test-service", "test-account"),
        credential_store=store,
        environment=environment,
        config_path=tmp_path / "settings.ini",
    )
    config.config_revision = 7

    def unexpected_read(*args, **kwargs):
        pytest.fail("execution capture must not read configuration or credential providers")

    monkeypatch.setattr(store, "get", unexpected_read)
    monkeypatch.setattr(config._repository, "load", unexpected_read)
    monkeypatch.setattr(config._repository, "update_sections", unexpected_read)
    monkeypatch.setattr(paratranz, "default_credential_store", unexpected_read)
    monkeypatch.setattr(paratranz, "default_config_repository", unexpected_read)
    monkeypatch.setenv("TRANSBRIDGE_PARATRANZ_TOKEN", "later-global-environment-token")
    environment["TRANSBRIDGE_PARATRANZ_TOKEN"] = "later-provider-token"

    copied = config.copy_for_execution()
    config.headers["X-Project"] = "later"
    config.update_timeout(99)
    config.update_token("later-config-token")
    config.user_id = 99
    config.base_url = "https://later.invalid/api"

    assert copied is not config
    assert copied.token == (None if source == "missing" else token)
    assert copied.get_headers() == (
        {"X-Project": "captured"}
        if source == "missing"
        else {"X-Project": "captured", "Authorization": f"Bearer {token}"}
    )
    assert (copied.base_url, copied.timeout, copied.user_id) == ("https://example.invalid/api", 23, 41)
    assert copied.credential_ref == config.credential_ref
    assert copied.config_revision == 7
    assert not copied.credential_capability.writable
    assert token not in repr(copied)
    assert not (tmp_path / "settings.ini").exists()

    second_copy = copied.copy_for_execution()
    copied.headers["X-Project"] = "changed-copy"
    assert second_copy.headers == {"X-Project": "captured"}
    assert second_copy.token == (None if source == "missing" else token)
