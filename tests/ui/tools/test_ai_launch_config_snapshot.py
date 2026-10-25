"""Launch with real repository-backed settings, including their non-copyable locks."""

from tests.ui.tools import test_unified_task_runtime as support
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository

launch = support.launch
qapp = support.qapp


def test_bound_project_launch_snapshots_real_paratranz_config(launch, monkeypatch, tmp_path):
    original = ParatranzConfig(
        token="launch-test-token",
        user_id=42,
        base_url="https://example.invalid/api",
        timeout=19,
        extra_headers={"X-Project": "original"},
        credential_store=UnavailableCredentialStore(),
        environment={},
        config_path=tmp_path / "settings.ini",
    )
    launch.window._ctx.config = original
    monkeypatch.setattr("transbridge.ui.paratranz.target_context.bound_paratranz_project", lambda _: {"id": 42})

    launch.start()

    assert not launch.messages
    (progress,) = launch.progress
    try:
        snapshot = progress.client.config
        original.update_token("later-token")
        original.add_header("X-Project", "later")
        original.base_url = "https://later.invalid/api"
        original.update_timeout(99)
        original.user_id = 99
        assert progress.running
        assert snapshot is not original
        assert snapshot.get_headers() == {"Authorization": "Bearer launch-test-token", "X-Project": "original"}
        assert (snapshot.base_url, snapshot.timeout, snapshot.user_id) == ("https://example.invalid/api", 19, 42)
    finally:
        progress.client.close()


def test_launch_uses_execution_copy_for_repository_backed_llm_config(launch, tmp_path):
    repository = ConfigRepository(
        path=tmp_path / "settings.ini",
        legacy_path=tmp_path / "legacy.ini",
        credential_store=UnavailableCredentialStore(),
    )
    repository.update_sections({
        "llm": {"provider": "openai", "base_url": "https://example.invalid/v1", "model": "test-model"}
    })
    original = type(launch.config).load_from_file(
        repository=repository, environment={"TRANSBRIDGE_LLM_API_KEY": "test-key"}
    )
    launch.window._config_presenter.build = lambda: original

    launch.start()

    assert not launch.messages
    (progress,) = launch.progress
    assert progress.running
    assert progress.request.config.model == "test-model"
    assert progress.request.config._repository is None
