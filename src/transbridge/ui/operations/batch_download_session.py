"""Compose multi-source download review, scoped recovery and one local commit."""

from __future__ import annotations

from collections import Counter
from copy import copy
from dataclasses import replace
from datetime import datetime
import logging
from threading import Lock
from uuid import uuid4

from PyQt6.QtWidgets import QMessageBox

from transbridge.application.categorized_upload import UploadSource
from transbridge.application.categorized_upload.classification import source_directory_names
from transbridge.application.sync import ConflictPolicy, SyncAction
from transbridge.application.sync.batch_download import (
    BatchDownloadSource,
    plan_batch_download,
    rebased_candidates,
    snapshots_from_entries,
)
from transbridge.application.sync.download_review import DownloadDecision
from transbridge.application.tasks import TaskCancelled
from transbridge.paratranz.batch_download_snapshot import BatchDownloadScope, BatchDownloadSnapshotReader
from transbridge.paratranz.categorized_upload import UploadCancellation
from transbridge.paratranz.download_paths import legacy_plugin_paths, unambiguous_legacy_paths
from transbridge.paratranz.path_snapshot import current_category_paths
from transbridge.paratranz.service import ParaTranzService
from transbridge.paratranz.sync_diagnostics import log_sync_diagnostics

from .batch_download_commit import publish_download, run_download_task
from .batch_download_dialog import BatchDownloadDialog
from .batch_download_view import DownloadPreview, DownloadReceipt, DownloadSourceOption
from .sync_review_projection import review_rows

_logger = logging.getLogger(__name__)
_DOWNLOAD_FEEDBACK = {
    "download_original_match": "按原文匹配",
    "download_translation_consensus": "合并相同译文",
    "download_key_match": "按原文和词条匹配",
    "download_current_translation_match": "按已有译文匹配",
    "no_matching_original": "无一致原文，保留本地",
    "remote_untranslated": "云端无译文，保留本地",
    "multiple_remote_translations": "不同译文待确认",
    "local_content_changed": "同步期间新编辑待确认",
}


class BatchDownloadSession:
    """Keep reviewed remote content while refreshing only mutable local state."""

    def __init__(self, context, runtime, *, reader_factory=BatchDownloadSnapshotReader, service_factory=None):
        if not getattr(context, "uses_authoritative_projection", False) or context.project_commands is None:
            raise ValueError("请先打开一个本地工程，再选择要下载的插件。")
        self.context, self.runtime = context, runtime
        self.target = context.resolve_paratranz_target()
        self.config = copy(context.config)
        self.stamp = self._target_stamp()
        if self.target.project_id is None or self.target.account_user_id is None:
            raise ValueError(self.target.reason or "请先登录并为当前工程选择 ParaTranz 项目。")
        self.request_context = replace(
            context.runtime_context,
            project_id=context.active_project_id,
            variant_id=context.active_variant_id,
        )
        self.reader_factory = reader_factory
        self.service_factory = service_factory or ParaTranzService.from_config
        self._reads, self._verified = {}, False
        self._scopes = {}
        self._lock = Lock()
        self.plan = self.snapshot = None
        self.source_ids, self.decisions = (), ()
        self.policy = ConflictPolicy.PREFER_REMOTE
        self.review_keys = ()
        self.details = ()
        self._retryable = False
        self._recovery = None

    def _target_stamp(self):
        target = self.context.resolve_paratranz_target()
        return (
            target.endpoint,
            target.project_id,
            target.account_user_id,
            getattr(self.context.config, "config_revision", 0),
            self.context.active_project_id,
            self.context.active_variant_id,
        )

    def _check_target(self):
        if self._target_stamp() != self.stamp:
            raise ValueError("当前工程、翻译版本或云端账号已切换，本轮未修改译文；请切回原目标后重试。")
        current = self.context.resolve_paratranz_target()
        if current.status.value not in {"available", "unverified"}:
            raise ValueError(current.reason or "当前账号无法访问所选 ParaTranz 项目。")

    def _capture(self, source_ids=None):
        self._check_target()
        for attempt in range(2):
            result = self.context.project_commands.file_sources_snapshot(
                self.request_context,
                endpoint=self.target.endpoint,
                remote_project_id=self.target.project_id,
                source_ids=source_ids,
            )
            if result.is_success and result.value is not None:
                return result.value
            stale = any(item.code == "CATEGORIZED_UPLOAD_STALE" for item in result.diagnostics)
            if not stale or attempt:
                raise RuntimeError("；".join(item.message for item in result.diagnostics) or "无法读取当前工程来源。")

    def load_sources(self, progress, cancel):
        progress(0, 0, "正在读取当前版本的插件目录…")
        if cancel.is_set():
            raise TaskCancelled("已取消读取插件。")
        for attempt in range(2):
            self._check_target()
            if cancel.is_set():
                raise TaskCancelled("已取消读取插件。")
            result = self.context.project_commands.file_sources_catalog(self.request_context)
            stale = any(item.code == "CATEGORIZED_UPLOAD_STALE" for item in result.diagnostics)
            if not stale or attempt:
                break
        _require_success(result, "无法读取当前版本的插件目录。")
        entries = tuple(result.value or ())
        current = {entry.identity.namespace for entry in (getattr(self.context, "collection", None) or ())}
        return tuple(
            DownloadSourceOption(
                source.source_id,
                source.label,
                source.count,
                selected=len(entries) == 1 or len(current) == 1 and source.namespace in current,
            )
            for source in entries
        )

    def _verify(self, cancel):
        if self._verified:
            return
        if cancel.is_set():
            raise TaskCancelled("下载已取消。")
        service = self.service_factory(self.config)
        try:
            projects = service.list_projects(uid=self.target.account_user_id, cancellation=UploadCancellation(cancel))
            if not any(project.project_id == self.target.project_id for project in projects):
                raise PermissionError(f"当前账号无权访问“{self.target.project_name}”。")
            self._verified = True
        finally:
            service.close()

    def compare(self, source_ids, policy, decisions, progress, cancel):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("正在处理当前下载，请等待完成。")
        try:
            return self._compare(tuple(source_ids), policy, tuple(decisions), progress, cancel)
        finally:
            self._lock.release()

    def _compare(self, source_ids, policy, decisions, progress, cancel):
        if not source_ids:
            raise ValueError("请至少选择一个插件。")
        self._check_target()
        self._verify(cancel)
        snapshot = self._capture(source_ids)
        scopes, sources, details = [], {}, []
        retryable = False
        names = self._source_names(snapshot)
        legacy_roots = self._legacy_roots(names)
        for source in snapshot.sources:
            if not source.entries:
                details.append(f"{source.label}：当前来源没有可更新词条，已跳过。")
                continue
            try:
                scope = self._scope(snapshot, source, names, legacy_roots)
            except (IndexError, ValueError) as exc:
                details.append(f"{source.label}：未采用，{exc}")
                retryable = True
                continue
            scopes.append(scope)
            sources[source.source_id] = source
        for _source_id, label, error in snapshot.failures:
            details.append(f"{label}：本地来源读取失败，未修改；{error}")
            retryable = True
        known = {source.source_id for source in snapshot.sources} | {
            source_id for source_id, _label, _error in snapshot.failures
        }
        retryable = retryable or bool(set(source_ids) - known)
        details.extend(
            f"来源 {source_id} 已移除或停用，本次跳过。" for source_id in source_ids if source_id not in known
        )
        missing = [scope for scope in scopes if scope not in self._reads]
        if missing:
            reads = self.reader_factory(self.config).fetch(
                self.target.project_id,
                missing,
                cancellation=UploadCancellation(cancel),
                progress=progress,
            )
            for scope, read in zip(missing, reads, strict=True):
                if not read.error and read.paths:
                    self._reads[scope] = read
                elif not read.error:
                    retryable = True
                    expected = "、".join((scope.paths[0], *scope.legacy_paths[:1]))
                    details.append(
                        f"{sources[scope.source_id].label}：云端未找到匹配文件（{expected}），已跳过；可原地重试。"
                    )
                else:
                    retryable = True
                    details.append(f"{sources[scope.source_id].label}：下载失败，未修改；{read.error}")
        complete = []
        invalid = self._invalid_cached_sources(scopes)
        cached_count = 0
        for scope in scopes:
            read = self._reads.get(scope)
            if read is None:
                continue
            source = sources[scope.source_id]
            if scope.source_id in invalid:
                retryable = True
                details.append(f"{source.label}：文件或云端条目与其他所选来源重叠，已排除该来源。")
                self._reads.pop(scope, None)
                continue
            if cached_count + len(read.entries) > 100_000:
                retryable = True
                details.append(f"{source.label}：超过本轮完整下载词条上限，本次未采用。请减少所选插件后重试。")
                continue
            cached_count += len(read.entries)
            if not read.paths:
                retryable = True
                details.append(f"{source.label}：云端没有当前路径文件，已跳过。")
                continue
            details.extend(f"{source.label}：采用 {path}" for path in read.paths)
            complete.append(
                BatchDownloadSource(
                    source.source_id,
                    source.label,
                    snapshots_from_entries(source.entries),
                    read.entries,
                )
            )
        if cancel.is_set():
            raise TaskCancelled("下载已取消，本地译文未修改。")
        progress(0, 0, "正在按原文和译文自动匹配…")
        self.plan = plan_batch_download(
            tuple(complete),
            policy=ConflictPolicy(policy),
            decisions=tuple(DownloadDecision(*decision) for decision in decisions),
            review_keys=self.review_keys,
        )
        self.snapshot, self.source_ids, self.policy, self.decisions = snapshot, source_ids, policy, decisions
        self._retryable = retryable
        self._scopes = {scope.source_id: scope for scope in scopes}
        self.details = self._plan_details(details)
        return self._preview()

    def _source_names(self, snapshot):
        return source_directory_names(
            tuple(UploadSource(source_id, label, ()) for source_id, label in snapshot.source_names)
        )

    def _legacy_roots(self, names):
        return unambiguous_legacy_paths({source_id: (name + ".json",) for source_id, name in names.items()})

    def _scope(self, snapshot, source, names=None, legacy_roots=None):
        names = self._source_names(snapshot) if names is None else names
        legacy_roots = self._legacy_roots(names) if legacy_roots is None else legacy_roots
        root = names[source.source_id] + ".json"
        paths = (root, *current_category_paths(root, source.entries))
        aliases = legacy_plugin_paths(paths)
        allowed = aliases if legacy_roots[source.source_id] else ()
        return BatchDownloadScope(
            source.source_id,
            source.entries[0].identity.namespace,
            paths,
            legacy_paths=allowed,
            blocked_legacy_paths=() if allowed else aliases,
        )

    def _invalid_cached_sources(self, scopes):
        """Validate ownership across cached and newly selected sources together."""
        paths, references = {}, {}
        for scope in scopes:
            read = self._reads.get(scope)
            if read is None:
                continue
            for path in read.paths:
                paths.setdefault(path, set()).add(scope.source_id)
            for entry in read.entries:
                if entry.external_ref is not None:
                    references.setdefault(entry.external_ref.index_key, set()).add(scope.source_id)
        return {source for owners in (*paths.values(), *references.values()) if len(owners) > 1 for source in owners}

    def _plan_details(self, details):
        details = list(details)
        for source, plan in zip(self.plan.sources, self.plan.plans, strict=True):
            try:
                log_sync_diagnostics(
                    plan,
                    project_id=self.target.project_id,
                    local_count=len(source.local_entries),
                    stale_decisions=bool(self.plan.cleared_decisions),
                )
            except Exception:
                _logger.exception("Download diagnostics failed; the content plan remains available")
            counts = dict(plan.counts)
            reasons = Counter(item.reason for item in plan.items)
            handled = "；".join(
                f"{label} {reasons[code]} 条" for code, label in _DOWNLOAD_FEEDBACK.items() if reasons[code]
            )
            message = f"{source.label}：预计更新 {counts.get('update_local', 0)}，待确认 {plan.conflicts}。"
            details.append(message + (" " + handled + "。" if handled else ""))
        if self.plan.cleared_decisions:
            details.append(f"已自动清理 {len(self.plan.cleared_decisions)} 个失效选择，其他有效选择保留。")
        return tuple(details)

    def _preview(self, status=""):
        unchanged = 0
        for source, plan in zip(self.plan.sources, self.plan.plans, strict=True):
            keys = {entry.entry_key for entry in source.local_entries}
            unchanged += sum(item.action is SyncAction.SKIP and item.entry_key in keys for item in plan.items)
        return DownloadPreview(
            self.plan.updated_count,
            unchanged,
            self.plan.deferred_count,
            self.details,
            tuple(row for plan in self.plan.plans for row in review_rows(plan)),
            bool(self.plan.sources),
            status,
            can_save=bool(self.plan.sources) and self.snapshot.dirty,
            retryable=self._retryable,
        )

    def apply(self, progress, cancel):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("正在处理当前下载，请等待完成。")
        try:
            if self.plan is None:
                raise ValueError("请先开始对照。")
            fingerprint = ":".join(plan.plan_hash for plan in self.plan.plans) or "empty"
            return run_download_task(
                self.runtime.tasks,
                self.request_context,
                fingerprint,
                lambda task_context, commit, cancellation: self._apply(
                    progress,
                    cancel,
                    task_context,
                    commit,
                    cancellation,
                ),
                cancel,
            )
        finally:
            self._lock.release()

    def _apply(self, progress, cancel, task_context, commit, cancellation):
        for attempt in range(2):
            cancellation.raise_if_cancelled()
            if cancel.is_set():
                raise TaskCancelled("下载已取消，本地译文未修改。")
            current = self._capture(self.source_ids)
            available = {source.source_id: source for source in current.sources}
            current_sources = tuple(
                replace(source, local_entries=snapshots_from_entries(available[source.source_id].entries))
                for source in self.plan.sources
                if source.source_id in available
            )
            candidate = rebased_candidates(self.plan, current_sources)
            missing = {source.source_id for source in self.plan.sources} - set(available)
            names = self._source_names(current)
            legacy_roots = self._legacy_roots(names)
            paths_changed = any(
                self._scope(current, source, names, legacy_roots) != self._scopes.get(source.source_id)
                for source in available.values()
                if source.entries
            )
            if candidate.deferred_keys or candidate.replan_source_ids or missing or paths_changed:
                self.review_keys = tuple(set(self.review_keys) | set(candidate.deferred_keys))
                preview = self._compare(self.source_ids, self.policy, self.decisions, progress, cancel)
                preview = replace(preview, status="内容已变化，已自动重新对照；新编辑可逐条复核，其他确定项可先更新。")
                return DownloadReceipt(0, preview.details, True, preview, retryable=preview.retryable)
            signature = (current.project_id, current.variant_id, current.project_revision, current.variant_revision)
            if self._recovery is None or self._recovery[0] != signature:
                recovery_name = f"ParaTranz-批量下载前-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
                progress(0, 1, "正在创建下载前历史还原点…")
                snapshot_result = self.context.project_commands.save_snapshot(recovery_name, task_context)
                _require_success(snapshot_result, "无法创建历史还原点，本地译文未修改。")
                self._recovery = (signature, recovery_name)
            recovery_name = self._recovery[1]

            def guarded_commit(mutation):
                def publish():
                    self._check_target()
                    mutation()

                commit(publish)

            progress(0, 1, "正在更新并保存当前本地版本…")
            result = publish_download(
                self.context,
                current,
                candidate.entries,
                candidate.updated_keys,
                self.target.project_id,
                task_context,
                guarded_commit,
            )
            if result.is_success:
                count = len(candidate.updated_keys)
                self.plan = None
                updated_keys = frozenset(candidate.updated_keys)
                self.review_keys = tuple(key for key in self.review_keys if key not in updated_keys)
                try:
                    progress(1, 1, "译文已保存到当前本地版本。")
                except Exception:
                    _logger.exception("Batch download committed but progress presentation failed")
                return DownloadReceipt(
                    count,
                    (*self.details, f"已更新并保存 {count} 条译文到当前本地版本；下载前历史还原点：{recovery_name}"),
                    retryable=self._retryable,
                )
            stale = any(
                item.code
                in {"ACTIVE_PROJECT_REVISION_CHANGED", "ACTIVE_VARIANT_REVISION_CHANGED", "SOURCE_UPDATE_STALE"}
                for item in result.diagnostics
            )
            if not stale:
                _require_success(result, "更新并保存失败，原工作内容和下载前还原点已保留，可在此重试。")
            if attempt:
                preview = self._compare(self.source_ids, self.policy, self.decisions, progress, cancel)
                preview = replace(preview, status="本地内容持续变化，已保留选择并重新对照；可确认后重试。")
                return DownloadReceipt(0, preview.details, True, preview, retryable=preview.retryable)


def _require_success(result, message):
    if not result.is_success:
        detail = "；".join(item.message for item in result.diagnostics)
        raise RuntimeError(f"{message} {detail}")


def open_batch_download(context, runtime, parent=None):
    try:
        session = BatchDownloadSession(context, runtime)
    except (ValueError, RuntimeError, AttributeError) as exc:
        QMessageBox.warning(parent, "无法开始下载", str(exc))
        return None
    dialog = BatchDownloadDialog(
        context.project_name or "当前工程",
        str(context.active_variant or "当前版本"),
        session.target.project_name or str(session.target.project_id),
        load_sources=session.load_sources,
        compare=session.compare,
        apply=session.apply,
        parent=parent,
    )
    dialog.show()
    return dialog
