# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""A single file's comparison, identity migration and ordered write phases."""

from transbridge.application.categorized_upload.diagnostics import log_failure
from transbridge.application.file_upload_diff import FileUploadChanges, IncompleteUploadSnapshot, plan_file_changes
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError


def comparison_plan(project_id, file_id, records, port, mode, archive_reader=None):
    """Archive exports omit some authoritative fields; supplement only that file."""
    if file_id is None:
        return FileUploadChanges(
            source_changed=mode in {"orig_only", "both"}, translations=records if mode == "both" else ()
        )
    if mode == "trans_force" and not any(row["translation"].strip() for row in records):
        return FileUploadChanges()
    if archive_reader is not None:
        try:
            rows = archive_reader(project_id, file_id)
            return plan_file_changes(records, rows, file_id=file_id, mode=mode, from_archive=True)
        except IncompleteUploadSnapshot:
            pass
    rows = port.get_file_translation(project_id, file_id)
    return plan_file_changes(records, rows, file_id=file_id, mode=mode)


def upload_one(
    project_id,
    path,
    records,
    inventory,
    port,
    mode,
    cancelled,
    check_current,
    *,
    archive_reader=None,
    defer_renames=None,
):
    from .file_upload import FileReceipt

    source_applied = translation_applied = renamed = attempted = False
    phase = "comparison"
    for attempt in range(2):
        try:
            remote_path, file_id = inventory.resolve(path)
        except ValueError as exc:
            log_failure("file_upload_resolve", exc, path=path, project_id=project_id)
            return FileReceipt(path, "failed", str(exc), source_applied or renamed, translation_applied)
        try:
            if mode in {"trans_safe", "trans_force"} and file_id is None:
                return FileReceipt(path, "skipped", "仅导入译文：云端没有此文件，已跳过。")
            if mode == "trans_force" and not any(row["translation"].strip() for row in records):
                return FileReceipt(path, "unchanged", "没有可强制导入的非空差异译文。")
            phase = "comparison"
            check_current()
            _check_cancelled(cancelled)
            plan = comparison_plan(project_id, file_id, records, port, mode, archive_reader if attempt == 0 else None)
            if plan.renames:
                if not plan.source_changed and not plan.translations and defer_renames is not None:
                    deferred = defer_renames(plan.renames)
                    if deferred is not None:
                        return deferred
                phase = "rename"
                check_current()
                _check_cancelled(cancelled)
                attempted = True
                # The gateway returns only after every requested ID/key is confirmed.
                port.rename_keys(project_id, plan.renames)
                renamed = True
            results = []
            if plan.source_changed:
                phase = "source"
                check_current()
                _check_cancelled(cancelled)
                attempted = True
                if file_id is None:
                    result = port.create(project_id, path, records)
                    _validate_write(result)
                    if type(result.file_id) is not int or result.file_id < 1:
                        raise ValueError("创建响应未返回文件 ID，结果待核实。")
                    file_id = result.file_id
                    inventory.record_created(path, file_id)
                else:
                    result = port.update(project_id, file_id, path, records)
                    _validate_write(result)
                results.append(result.status)
                source_applied |= result.status == "applied"
            translations = plan.translations
            if mode != "orig_only" and translations:
                phase = "translation"
                check_current()
                _check_cancelled(cancelled)
                attempted = True
                result = translate_parts(
                    project_id, file_id, path, translations, port, mode == "trans_force", cancelled, check_current
                )
                results.append(result.status)
                translation_applied |= result.status == "applied"
            unchanged = not renamed and all(status in {"hashMatched", "unchanged"} for status in results)
            message = "本地与云端内容一致，无需更新。" if unchanged else "文件操作完成。"
            if mode in {"both", "trans_safe"} and translations:
                message += "译文使用安全导入，人工译文等由服务端保留。"
            return FileReceipt(
                path, "unchanged" if unchanged else "succeeded", message, source_applied or renamed, translation_applied
            )
        except ExternalServiceError as exc:
            renamed |= phase == "rename" and bool(getattr(exc, "confirmed_ids", ()))
            translation_applied |= bool(getattr(exc, "translation_applied", False))
            log_failure("file_upload_request", exc, path=path, project_id=project_id)
            if exc.status == 404 and attempt == 0:
                try:
                    inventory.relocate(port, project_id, remote_path)
                except Exception as refresh_error:
                    log_failure("file_upload_relocate", refresh_error, path=path)
                    return FileReceipt(
                        path,
                        "failed",
                        _committed_message(f"重新定位文件失败：{refresh_error}", renamed, source_applied),
                        source_applied or renamed,
                        translation_applied,
                    )
                continue
            if exc.status == 413:
                # Only the source-file import owns file splitting. Reads and ID
                # migrations must never accidentally publish additional files.
                if phase == "source" and not (renamed or source_applied or translation_applied):
                    return FileReceipt(path, "too_large", "文件过大。", source_applied, translation_applied)
                operation = (
                    "云端比较读取" if phase == "comparison" else "排序 key 更新" if phase == "rename" else "文件操作"
                )
                message = f"{operation}超过服务器大小限制，未创建其他文件。"
                status = "failed"
            elif exc.category is ExternalServiceCategory.CANCELLED:
                status, message = "cancelled", "请求已取消；已提交的文件操作可能已生效。"
            elif phase == "comparison":
                status, message = "failed", f"无法读取云端文件进行比较，后续写入已停止：{exc}"
            elif exc.status == 403:
                status, message = "failed", "权限不足：文件操作需要相应管理员权限，未回退到词条接口。"
            elif exc.status == 423:
                status, message = "failed", "文件正在被其他用户更新或回滚，请稍后重试。"
            else:
                uncertain = exc.status is None or exc.status >= 500
                status = "uncertain" if uncertain else "failed"
                message = f"文件操作{'结果待核实，不自动重发' if uncertain else '失败'}：{exc}"
            return FileReceipt(
                path,
                status,
                ("部分译文已提交；" if translation_applied else "")
                + _committed_message(message, renamed, source_applied),
                source_applied or renamed,
                translation_applied,
                exc.status,
            )
        except Exception as exc:
            renamed |= phase == "rename" and bool(getattr(exc, "confirmed_ids", ()))
            translation_applied |= bool(getattr(exc, "translation_applied", False))
            log_failure("file_upload_request", exc, path=path, project_id=project_id)
            message = (
                f"无法比较云端文件，后续写入已停止：{exc}" if phase == "comparison" else f"文件操作未完整完成：{exc}"
            )
            return FileReceipt(
                path,
                "uncertain" if attempted and phase != "comparison" else "failed",
                ("部分译文已提交；" if translation_applied else "")
                + _committed_message(message, renamed, source_applied),
                source_applied or renamed,
                translation_applied,
            )
    raise RuntimeError("unreachable file upload state")


def _check_cancelled(cancelled):
    if cancelled():
        raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "文件上传已取消。")


def _committed_message(message, renamed, source_applied):
    return ("排序 key 已提交；" if renamed else "") + ("原文已提交；" if source_applied else "") + message


def _validate_write(result):
    if getattr(result, "status", None) not in {"applied", "hashMatched", "unchanged"}:
        raise ValueError("文件操作未返回有效成功确认，结果待核实。")


def translate_parts(project_id, file_id, path, records, port, force, cancelled, check_current):
    """An oversized translation import is chunked inside the SAME remote file."""
    from .file_upload import FileWriteResult

    check_current()
    _check_cancelled(cancelled)
    try:
        result = port.translate(project_id, file_id, path, records, force=force)
        _validate_write(result)
        return result
    except ExternalServiceError as exc:
        if exc.status != 413 or len(records) <= 1:
            raise
    middle = len(records) // 2
    first = translate_parts(project_id, file_id, path, records[:middle], port, force, cancelled, check_current)
    try:
        second = translate_parts(project_id, file_id, path, records[middle:], port, force, cancelled, check_current)
    except Exception as exc:
        # Keep the original category/status so cancellation and global rejection
        # still stop the other pipelines, but never replay completed chunks.
        if isinstance(exc, ExternalServiceError):
            error = ExternalServiceError(
                exc.category,
                "部分译文已处理，后续导入未完成；请查看文件结果。",
                status=exc.status,
            )
        else:
            error = RuntimeError("部分译文已处理，后续导入未完成；请查看文件结果。")
        error.translation_applied = first.status == "applied" or bool(getattr(exc, "translation_applied", False))
        raise error from exc
    applied = first.status == "applied" or second.status == "applied"
    return FileWriteResult("applied" if applied else "unchanged", file_id)
