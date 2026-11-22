# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Adapt the workbench artifact publisher to Assistant arguments and file grants."""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.sync import ArtifactPublishRequest, ParaTranzArtifactPublisher

from .base import ToolResult
from .file_undo_capture import authorize_file_outputs


def _file_path(path: Path) -> Path:
    if path.is_symlink() or not path.parent.is_dir() or (path.exists() and not path.is_file()):
        raise RequestError("ARTIFACT_OUTPUT_INVALID", "请选择已存在目录中的普通输出文件。")
    return path.resolve()


@dataclass(frozen=True, slots=True)
class SelectedArtifactOutput:
    path: Path
    stamp: tuple[int, int, int, int, int] | None

    @classmethod
    def capture(cls, selected: str):
        path = Path(selected)
        if not path.is_absolute():
            raise RequestError("ARTIFACT_OUTPUT_INVALID", "文件选择器未返回绝对路径。")
        target = _file_path(path)
        try:
            stat = target.stat()
        except FileNotFoundError:
            return cls(target, None)
        return cls(target, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))

    def verify(self):
        if self.capture(str(self.path)) != self:
            raise RequestError("ARTIFACT_OUTPUT_CHANGED", "所选文件已变化，请重新选择保存位置。")


class _ArtifactFileCommit:
    def __init__(self, ctx, path: Path, guard, selected: SelectedArtifactOutput | None):
        self._ctx = ctx
        self._path = path
        self._guard = guard
        self._selected = selected

    def commit(self, run_id, mutation):
        def replace():
            if self._selected is not None:
                self._selected.verify()
            elif _file_path(authorize_file_outputs(self._ctx, [self._path])[0]) != self._path:
                raise RequestError("ARTIFACT_OUTPUT_CHANGED", "下载位置已变化。")
            mutation()

        return self._guard.commit(run_id, replace)


def export_artifact(args, ctx, client, pid: int) -> ToolResult:
    action = args.get("action", "trigger")
    cancellation = getattr(ctx, "cancellation_token", None)
    publisher = ParaTranzArtifactPublisher(client)
    gate = getattr(ctx, "assistant_gate", None)
    effect_id = getattr(ctx, "assistant_effect_id", "")
    missing_gate = gate is None and (effect_id or getattr(ctx, "assistant_required", False))
    if missing_gate or (gate is not None and not effect_id):
        return ToolResult.fail("请求执行许可缺失。", error_category="permission", error_code="TURN_LEASE_STALE")
    if action == "trigger":
        if "output_path" in args:
            return ToolResult.fail("保存已有导出包时请使用 download。", error_category="input")
        metadata, job = publisher.trigger_and_wait(
            pid,
            cancellation=cancellation,
            poll_interval_seconds=2,
            max_poll_attempts=16,
            timeout_seconds=30,
            before_submit=(lambda: gate.validate_start(effect_id)) if gate is not None else None,
        )
        if metadata is None:
            return ToolResult.ok("已提交导出，等待新工件超时。", data={"job": dict(job), "status": "pending"})
        return ToolResult.ok("工件导出完成", data=metadata)
    if action != "download":
        return ToolResult.fail("action 必须为 trigger 或 download。", error_category="input")
    value = args.get("output_path")
    choose = getattr(ctx, "choose_artifact_output", None)
    selected = None
    try:
        if callable(choose):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            suggestion = Path(value).name if isinstance(value, str) else ""
            selected = choose(suggestion if suggestion not in {"", ".", ".."} else "export.zip")
            if selected is None:
                return ToolResult.fail("已取消选择保存位置。", error_code="ARTIFACT_DOWNLOAD_CANCELLED")
            if not isinstance(selected, SelectedArtifactOutput):
                raise RequestError("ARTIFACT_OUTPUT_INVALID", "文件选择器未提供有效的保存位置。")
            target = selected.path
            selected.verify()
        else:
            if not isinstance(value, str) or not value.strip():
                return ToolResult.fail("下载已有导出包需要指定 output_path。", error_category="input")
            target = _file_path(authorize_file_outputs(ctx, [value])[0])
        if gate is not None:
            gate.validate_start(effect_id)
    except RequestError as error:
        return ToolResult.fail(str(error), error_code=error.code, error_category="input")

    if gate is None:
        effect_id = uuid4().hex
        gate = ImmediateCommitGuard(effect_id)
    result = publisher.publish(
        ArtifactPublishRequest(
            pid, str(target), effect_id, _ArtifactFileCommit(ctx, target, gate, selected), cancellation
        ),
        download_existing=True,
    )
    if result.outcome is OperationOutcome.CANCELLED and cancellation is not None:
        cancellation.raise_if_cancelled()
    if result.outcome is OperationOutcome.COMPLETED:
        return ToolResult.ok("已下载现有导出包。", data={"output_path": str(target)})
    diagnostic = result.diagnostics[0]
    if result.outcome is OperationOutcome.PARTIAL:
        return ToolResult.partial_ok(diagnostic.message, data={"output_path": str(target)})
    return ToolResult.fail(diagnostic.message, error_code=diagnostic.code)
