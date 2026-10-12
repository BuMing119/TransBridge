"""Project FOMOD reports into the operation result presentation contract."""

from transbridge.application.contracts import OperationCounts, OperationOutcome, OperationResult


def fomod_operation_result(report):
    artifacts = tuple(item.location for item in report.artifacts if item.kind == "published-archive")
    committed = report.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}
    return OperationResult(
        report.outcome,
        value={} if committed else None,
        counts=OperationCounts(
            succeeded=max(1, len(artifacts)) if committed else 0,
            failed=int(report.outcome in {OperationOutcome.FAILED, OperationOutcome.PARTIAL}),
            cancelled=int(report.outcome is OperationOutcome.CANCELLED),
        ),
        diagnostics=report.diagnostics,
        artifact_refs=artifacts,
        run_id=report.run_id,
    )


def fomod_check_message(code):
    labels = {
        "FOMOD_OUTPUT_FORMAT_UNSUPPORTED": "请选择 ZIP 或 7Z 输出格式。",
        "FOMOD_CONFIG_INCOMPLETE": "请选择目标语言并补齐构建选项。",
        "FOMOD_OUTPUT_UNWRITABLE": "输出目录不存在或不可写，请选择其他输出位置。",
        "FOMOD_OVERWRITE_CONFIRMATION_REQUIRED": "输出文件已存在，请确认覆盖或更改输出位置。",
    }
    for prefix, name in (("NEW", "新版"), ("OLD", "旧版")):
        labels.update({
            f"FOMOD_{prefix}_ARCHIVE_UNREADABLE": f"无法读取{name}归档，请检查文件路径和权限。",
            f"FOMOD_{prefix}_ARCHIVE_EMPTY": f"{name}归档为空，请重新选择。",
            f"FOMOD_{prefix}_ARCHIVE_POLICY_REJECTED": f"{name}归档未通过安全检查，请检查归档内容或重新打包。",
        })
    return labels.get(code, code)
