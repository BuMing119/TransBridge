"""User-facing completion receipts, separate from technical diagnostics."""

from dataclasses import dataclass

from transbridge.application.contracts import OperationResult
from transbridge.application.tasks import JobState

from .runtime_adapter import operation_object_results


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    status: str
    text: str
    details: str


def execution_receipt(activity, result: OperationResult | None, *, label: str, is_batch: bool) -> ExecutionReceipt:
    status = {
        JobState.FAILED: "failed",
        JobState.CANCELLED: "cancelled",
    }.get(activity.state, result.outcome.value if result is not None else "failed")
    headings = {
        "completed": f"{label}完成。",
        "partial": f"{label}部分完成，仍有文件需要处理。",
        "failed": f"{label}失败。",
        "cancelled": f"{label}已取消。",
    }
    lines = [headings[status]]
    details = [f"任务 ID：{activity.run_id}"]
    if result is None:
        lines.append("未取得执行结果，请在任务中心查看详情。")
        details.extend(item.code for item in activity.diagnostic_refs)
    else:
        value = result.value if isinstance(result.value, dict) else {}
        report = value.get("transfer_report")
        if isinstance(report, dict):
            if status == "completed":
                lines.append(
                    f"已套用 {report.get('writable_count', 0)} 条译文，目标保留 {report.get('preserved_count', 0)} 条。"
                )
            else:
                lines.append("套用未完整完成，请检查已产生文件。")
            for issue in report.get("issues", ()):
                if isinstance(issue, dict):
                    original = str(issue.get("original", ""))
                    excerpt = original[:120] + ("…" if len(original) > 120 else "")
                    details.append(f"{issue.get('key', '')} · 原文 {excerpt!r}：{issue.get('message', '')}")
        objects = operation_object_results(result)
        if is_batch:
            succeeded = sum(item.status.value == "succeeded" for item in objects)
            failed = sum(item.status.value == "failed" for item in objects)
            cancelled = sum(item.status.value == "cancelled" for item in objects)
            lines.append(f"已完成 {succeeded} 个文件，未完整完成 {failed} 个，已取消 {cancelled} 个。")
        if status != "completed":
            for diagnostic in result.diagnostics:
                if diagnostic.code in {"TRANSFER_PRESERVED", "WRITE_BATCH_RESULTS"}:
                    continue
                message = (
                    "文件已写出，但未能确认已安全保存；请检查输出文件后再决定是否重试。"
                    if diagnostic.code in {"DURABILITY_FAILED", "DIRECTORY_FSYNC_FAILED"}
                    else diagnostic.message
                )
                if message not in lines:
                    lines.append(message)
        else:
            for diagnostic in result.diagnostics:
                if diagnostic.code != "TRANSFER_PRESERVED" and diagnostic.message not in lines:
                    lines.append(diagnostic.message)
        names = {"succeeded": "已完成", "failed": "未完整完成", "cancelled": "已取消", "skipped": "已跳过"}
        if is_batch:
            lines.extend(f"{item.object_ref}：{names[item.status.value]}" for item in objects)
        if result.artifact_refs:
            remaining = tuple(
                path for path in result.artifact_refs if not is_batch or path not in {i.object_ref for i in objects}
            )
            if remaining:
                lines.append("已输出文件：" if status == "completed" else "已产生文件（需检查完整性）：")
                lines.extend(remaining)
        backup_paths = tuple(value.get("backup_paths", ()))
        if value.get("backup_path"):
            backup_paths = (*backup_paths, value["backup_path"])
        if backup_paths:
            lines.append("备份文件：")
            lines.extend(backup_paths)
        for diagnostic in result.diagnostics:
            recovery = dict(diagnostic.details).get("recovery_directory")
            if recovery:
                lines.append(f"恢复目录：{recovery}")
            if diagnostic.code != "TRANSFER_PRESERVED":
                details.append(f"{diagnostic.code}：{diagnostic.message}")
        details.extend(f"{item.object_ref}：{item.code}" for item in objects if item.code)
    if status != "completed" and is_batch:
        lines.append("此窗口再次执行会处理全部文件，包括已完成的文件。仅重试失败文件，请关闭后逐个写回。")
        lines.append("如需重新执行全部文件，请重新运行预检。")
    elif status != "completed":
        lines.append("如需重试，请重新运行预检。")
    return ExecutionReceipt(status, "\n".join(lines), "\n".join(details))
