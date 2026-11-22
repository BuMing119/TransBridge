"""Exercise the registered tool and the workbench command against identical HTTP responses."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from tests.conftest import MockAppContext
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.io.publish import CommitDecision
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI
from transbridge.paratranz.workflow.artifact import ArtifactWorkflow
from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_paratranz

ARTIFACTS = "/projects/7/artifacts"
JOB = "/projects/7/jobs/9"
DOWNLOAD = ARTIFACTS + "/download"


@pytest.fixture
def tools(tmp_path):
    if ToolRegistry.get("export_artifact") is None:
        tool_paratranz._register_paratranz_tools()
    spec = ToolRegistry.get("export_artifact")
    assert spec.available and spec.require_confirmation and spec.permission == "write"
    ctx = MockAppContext()
    ctx.authorized_roots = (str(tmp_path),)
    ctx._config = ParatranzConfig(credential_store=UnavailableCredentialStore(), environment={})
    return spec, ctx


def zip_bytes():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("entries.json", "[]")
    return buffer.getvalue()


def response_with(content):
    return SimpleNamespace(iter_content=lambda **kwargs: [content], close=lambda: None)


def test_trigger_and_existing_download_match_workbench_endpoint_calls(tools, monkeypatch, tmp_path):
    spec, ctx = tools
    entries = zip_bytes()
    calls = []

    def request(api, method, path, **kwargs):
        calls.append((method, path))
        if method == "POST":
            return {"id": 9, "status": 0}
        if path == JOB:
            return {"id": 9, "status": 1}
        if path == ARTIFACTS:
            return {"results": [{"id": 2 if ("GET", JOB) in calls else 1}]}
        assert path == DOWNLOAD
        return response_with(entries)

    monkeypatch.setattr(ParatranzExportAPI, "_request", request)
    desktop = ArtifactWorkflow(ctx.config)
    desktop_target = tmp_path / "desktop.zip"
    desktop.trigger_and_download(7, desktop_target)
    trigger_trace = [("GET", ARTIFACTS), ("POST", ARTIFACTS), ("GET", JOB), ("GET", ARTIFACTS)]
    assert calls == [*trigger_trace, ("GET", DOWNLOAD)]
    calls.clear()
    result = spec.execute({"project_id": "7"}, ctx)
    assert result.success and result.data == {"id": 2}
    assert calls == trigger_trace

    calls.clear()
    desktop.download_existing(7, desktop_target)
    assert calls == [("GET", DOWNLOAD)]
    calls.clear()
    args = {"project_id": "7", "action": "download", "output_path": "assistant.zip"}
    assert InputValidationGuard().before_execute({"tool": spec.name, "args": args}, ctx).allowed
    result = spec.execute(args, ctx)
    assert result.success and result.data == {"output_path": str(tmp_path / "assistant.zip")}
    assert (tmp_path / "assistant.zip").read_bytes() == desktop_target.read_bytes() == entries
    assert calls == [("GET", DOWNLOAD)]


@pytest.mark.parametrize(
    "options",
    [
        {"action": "unknown"},
        {"action": "trigger", "output_path": "file.zip"},
        {"action": "download"},
        {"action": "download", "output_path": ".."},
        {"action": "download", "output_path": "missing/target.zip"},
        {"action": "download", "output_path": "."},
    ],
)
def test_invalid_intent_or_target_never_contacts_server(tools, tmp_path, monkeypatch, options):
    spec, ctx = tools
    request = Mock()
    monkeypatch.setattr(ParatranzExportAPI, "_request", request)
    assert not spec.execute({"project_id": "7", **options}, ctx).success
    request.assert_not_called()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("failure", ["invalid_zip", "permission", "stale_approval", "cancel"])
def test_failed_download_preserves_user_file_and_removes_stage(tools, monkeypatch, tmp_path, failure):
    spec, ctx = tools
    target = tmp_path / "known.zip"
    target.write_bytes(b"existing file")
    ctx.cancellation_token = CancellationToken()
    if failure == "stale_approval":
        ctx.assistant_effect_id = "effect-1"
        ctx.assistant_gate = SimpleNamespace(
            validate_start=lambda _: None, commit=lambda *args: CommitDecision(False, "stale")
        )
    calls = []

    def request(api, method, path, **kwargs):
        calls.append((method, path))
        if failure == "permission":
            raise ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "forbidden", status=403)
        if failure == "cancel":
            ctx.cancellation_token._cancel("user stopped download")
        return response_with(b"invalid" if failure == "invalid_zip" else zip_bytes())

    monkeypatch.setattr(ParatranzExportAPI, "_request", request)
    args = {"project_id": "7", "action": "download", "output_path": str(target)}
    if failure == "cancel":
        with pytest.raises(TaskCancelled):
            spec.execute(args, ctx)
    else:
        result = spec.execute(args, ctx)
        assert not result.success
        assert (
            result.error_code
            == {
                "invalid_zip": "ARTIFACT_INVALID_ZIP",
                "permission": "REMOTE_AUTHORIZATION",
                "stale_approval": "ARTIFACT_COMMIT_REJECTED",
            }[failure]
        )
    assert calls == [("GET", DOWNLOAD)]
    assert list(tmp_path.iterdir()) == [target]
    assert target.read_bytes() == b"existing file"


def test_trigger_timeout_reports_job_and_never_resubmits(tools, monkeypatch):
    spec, ctx = tools
    calls = []

    def request(api, method, path, **kwargs):
        calls.append((method, path))
        return {"results": []} if (method, path) == ("GET", ARTIFACTS) else {"id": 9, "status": 0}

    monkeypatch.setattr(ParatranzExportAPI, "_request", request)
    monkeypatch.setattr("transbridge.application.sync.artifact.time.sleep", lambda _: None)
    result = spec.execute({"project_id": "7"}, ctx)
    assert result.success and result.data == {"job": {"id": 9, "status": 0}, "status": "pending"}
    assert calls.count(("POST", ARTIFACTS)) == 1
    assert ("GET", DOWNLOAD) not in calls


def test_trigger_rechecks_effect_after_baseline_before_post(tools, monkeypatch):
    spec, ctx = tools
    ctx.assistant_effect_id = "effect"
    ctx.assistant_gate = SimpleNamespace(validate_start=Mock(side_effect=RequestError("TURN_LEASE_STALE", "revoked")))
    request = Mock(return_value={"results": []})
    monkeypatch.setattr(ParatranzExportAPI, "_request", request)
    result = spec.execute({"project_id": "7"}, ctx)
    assert not result.success and result.error_code == "TURN_LEASE_STALE"
    request.assert_called_once()
    assert request.call_args.args == ("GET", ARTIFACTS)
    ctx.assistant_gate.validate_start.assert_called_once_with("effect")
