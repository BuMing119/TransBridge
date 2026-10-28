"""Compose multi-source download review, scoped recovery and one local commit."""

from __future__ import annotations

from collections import Counter
from copy import copy
from dataclasses import replace
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
    snapshots_from_entries,
)
from transbridge.application.sync.download_results import DownloadResultStore
from transbridge.application.sync.download_review import DownloadDecision
from transbridge.application.tasks import TaskCancelled
from transbridge.application.translation.result_merge import (
    ResultConflictPolicy,
    merge_result_entries,
)
from transbridge.config.task_results import TaskResultPreferences
from transbridge.paratranz.batch_download_snapshot import BatchDownloadScope, BatchDownloadSnapshotReader
from transbridge.paratranz.download_paths import legacy_plugin_paths, unambiguous_legacy_paths
from transbridge.paratranz.file_transport import UploadCancellation
from transbridge.paratranz.path_snapshot import current_category_paths
from transbridge.paratranz.service import ParaTranzService
from transbridge.paratranz.sync_diagnostics import log_sync_diagnostics

from .batch_download_access import export_result, partial_review_rows, restore_previous, restore_result
from .batch_download_commit import run_download_task
from .batch_download_dialog import BatchDownloadDialog
from .batch_download_results import (
    merge_options,
    merge_review_rows,
    preview_entry_states,
    publish_results,
    result_changes,
)
from .batch_download_view import DownloadPreview, DownloadSourceOption
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
        self._unsaved_reads = {}
        self._partial_reads = {}
        self._scopes = {}
        self._lock = Lock()
        self.plan = self.snapshot = None
        self.source_ids, self.decisions = (), ()
        self.policy = ConflictPolicy.PREFER_REMOTE
        self.review_keys = ()
        self.details = ()
        self._retryable = False
        provider = getattr(context, "task_result_preferences_provider", None)
        self.merge_policy = (provider() if provider else TaskResultPreferences()).download_policy
        self.default_policy = self.merge_policy.value
        self.result_store = DownloadResultStore(context.project_commands.task_result_directory(self.request_context))
        self.record = None
        self._bases = {}
        self.merge_result = None
        self._override_changes = None
        self.review_states = {}

    def _target_stamp(self):
        target = self.context.resolve_paratranz_target()
        return (
            target.endpoint,
            target.project_id,
            target.account_user_id,
            self.context.active_project_id,
            self.context.active_variant_id,
        )

    def _check_target(self):
        if self._target_stamp() != self.stamp:
            raise ValueError("当前工程、翻译版本或云端账号已切换，本轮未修改译文；请切回原目标后重试。")
        if self.context.resolve_paratranz_target().status.value in {"account_mismatch", "endpoint_mismatch", "unbound"}:
            raise ValueError("当前云端账号或服务身份已变化；下载成果保留，可切回原目标后处理。")

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
        self.merge_policy = _merge_policy(policy)
        if self.record is None:
            self.record = self.result_store.create(self.stamp, self.merge_policy)
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
                scope = self._scopes.get(source.source_id) or self._scope(snapshot, source, names, legacy_roots)
            except (IndexError, ValueError) as exc:
                details.append(f"{source.label}：未采用，{exc}")
                retryable = True
                continue
            scopes.append(scope)
            sources[source.source_id] = source
            self._bases.setdefault(
                source.source_id,
                BatchDownloadSource(source.source_id, source.label, snapshots_from_entries(source.entries), ()),
            )
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
        for scope, read in tuple(self._unsaved_reads.items()):
            self.result_store.save_source(self.record, scope, read, self._bases[scope.source_id])
            self._unsaved_reads.pop(scope)
        if missing:
            self._verify(cancel)

            def retain(scope, read):
                if not read.error and read.paths:
                    self._reads[scope] = read
                    self._partial_reads.pop(scope, None)
                else:
                    self._reads.pop(scope, None)
                    if read.paths:
                        self._partial_reads[scope] = read
                self._unsaved_reads[scope] = read
                self.result_store.save_source(self.record, scope, read, self._bases[scope.source_id])
                self._unsaved_reads.pop(scope)
                self._scopes[scope.source_id] = scope

            reads = self.reader_factory(self.config).fetch(
                self.target.project_id,
                missing,
                cancellation=UploadCancellation(cancel),
                progress=progress,
                on_source=retain,
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
                self.result_store.save_source(
                    self.record,
                    scope,
                    replace(read, entries=(), error="来源归属重叠，暂不可合入。"),
                    self._bases[scope.source_id],
                )
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
                    self._bases[source.source_id].local_entries,
                    read.entries,
                )
            )
        present_ids = {source.source_id for source in complete}
        for source_id in source_ids:
            if source_id in present_ids or source_id in sources:
                continue
            scope = self._scopes.get(source_id)
            read = self._reads.get(scope)
            if read is not None and not read.error and source_id in self._bases:
                complete.append(replace(self._bases[source_id], remote_entries=read.entries))
        if cancel.is_set():
            raise TaskCancelled("下载已取消，本地译文未修改。")
        progress(0, 0, "正在按原文和译文自动匹配…")
        self.plan = plan_batch_download(
            tuple(complete),
            policy=ConflictPolicy.PREFER_REMOTE,
            decisions=tuple(
                DownloadDecision(*decision) for decision in decisions if not decision[0].startswith("merge:")
            ),
            review_keys=self.review_keys,
        )
        self.snapshot, self.source_ids, self.policy, self.decisions = snapshot, source_ids, policy, decisions
        self._retryable = retryable
        self._scopes = {scope.source_id: scope for scope in scopes}
        self.details = self._plan_details(details)
        self.record["decisions"] = list(decisions)
        self.record["policy"] = self.merge_policy.value
        self.result_store.save(self.record)
        return self._preview()

    def saved_results(self):
        return self.result_store.list_records(self.stamp)

    def restore(self, result_id, progress, cancel):
        return restore_result(self, result_id, progress, cancel)

    def export_results(self, path):
        return export_result(self, path)

    def restore_previous(self, progress, cancel):
        return restore_previous(self, progress, cancel)

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
        candidates = result_changes(self.plan)
        policies, reviewed = merge_options(self, candidates)
        current = (
            {row.base.entry_key: row.base for row in candidates}
            if self.snapshot is None
            else preview_entry_states(
                self.snapshot, self.context.project_sources, {row.base.entry_key for row in candidates}
            )
        )
        merge = merge_result_entries(
            {row.base.entry_key: row.base for row in candidates},
            current,
            {row.incoming.entry_key: row.incoming for row in candidates},
            policy=self.merge_policy,
            policies=policies,
            decision_states=reviewed,
        )
        return DownloadPreview(
            len(merge.applied),
            unchanged + len(merge.unchanged_keys) + len(merge.kept_current_keys),
            self.plan.deferred_count
            + len(merge.pending)
            + sum(len(read.entries) for read in self._partial_reads.values()),
            self.details,
            (
                *tuple(row for plan in self.plan.plans for row in review_rows(plan)),
                *merge_review_rows(self, merge),
                *partial_review_rows(self._partial_reads),
            ),
            bool(self.plan.sources),
            status,
            can_save=bool(self.plan.sources) and self.snapshot is not None and self.snapshot.dirty,
            retryable=self._retryable,
            source_ids=self.source_ids,
            policy=self.merge_policy.value,
        )

    def apply(self, progress, cancel):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("正在处理当前下载，请等待完成。")
        try:
            if self.plan is None:
                raise ValueError("请先开始对照。")
            fingerprint = (":".join(plan.plan_hash for plan in self.plan.plans) or "empty") + ":" + uuid4().hex
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
        return publish_results(self, task_context, commit, progress, cancel, cancellation)


def _require_success(result, message):
    if not result.is_success:
        detail = "；".join(item.message for item in result.diagnostics)
        raise RuntimeError(f"{message} {detail}")


def _merge_policy(value):
    aliases = {"prefer_remote": "prefer_incoming", "prefer_local": "keep_current", "skip": "defer"}
    return ResultConflictPolicy(aliases.get(value, value))


def open_batch_download(context, runtime, parent=None, *, values=None):
    try:
        if values:
            from .download_context import DownloadContext

            context = DownloadContext(context, values)
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
        saved_results=getattr(session, "saved_results", None),
        restore=getattr(session, "restore", None),
        export_results=getattr(session, "export_results", None),
        restore_previous=getattr(session, "restore_previous", None),
        default_policy=getattr(session, "default_policy", "keep_current"),
        parent=parent,
    )
    dialog.show()
    return dialog
