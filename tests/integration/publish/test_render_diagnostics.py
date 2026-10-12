from types import SimpleNamespace

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import FormatId, SourceDescriptor, WriteRequest
from transbridge.application.io.publish import (
    ConflictPolicy,
    ImmediateCommitGuard,
    OsPublishFilesystem,
    PublishCoordinator,
    PublishTarget,
)


def test_render_failure_retains_format_diagnostic_without_replacing_target(tmp_path):
    target = tmp_path / "translated.esp"
    target.write_bytes(b"existing plugin")
    failed = OperationResult.failed(
        DomainError(ErrorCategory.CONFLICT, "SOURCE_LOCATOR_NOT_FOUND", "The source record could not be located.")
    )
    request = WriteRequest(
        SourceDescriptor(str(target)),
        FormatId.PLUGIN_SSE,
        (),
        1,
        RequestContext("test", run_id="test-render-failure"),
        new_template=b"plugin template",
    )
    result = PublishCoordinator(OsPublishFilesystem()).publish(
        request,
        PublishTarget(str(target), conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE),
        renderer=SimpleNamespace(render=lambda *_args: failed),
        validator=SimpleNamespace(),
        commit_guard=ImmediateCommitGuard("test-render-failure"),
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "RENDER_FAILED"
    assert "SOURCE_LOCATOR_NOT_FOUND" in result.message
    assert "source record could not be located" in result.message
    assert target.read_bytes() == b"existing plugin"
    assert sorted(tmp_path.iterdir()) == [target]
