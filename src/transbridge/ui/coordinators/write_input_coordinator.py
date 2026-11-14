# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve plugin references for a single export and remember verified repairs."""

from collections import Counter
from dataclasses import replace
import logging
from pathlib import Path
from uuid import uuid4

from PyQt6.QtWidgets import QFileDialog

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io.contracts import FormatId
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.write_formats import SST_FORMATS
from transbridge.application.projects.storage_contracts import ProjectReadToken
from transbridge.application.projects.write_inputs import bind_plugin_write_input
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.text_encoding import context_text_encodings

_logger = logging.getLogger(__name__)
_STALE = "工程版本已变化，请重新发起导出。"
_UNUSABLE_INPUT = frozenset({
    ErrorCategory.INPUT,
    ErrorCategory.PREREQUISITE,
    ErrorCategory.PERMISSION,
    ErrorCategory.CONFLICT,
})


def _unbound_plugins(slots):
    return tuple(
        key
        for key, slot in slots.items()
        if getattr(slot, "format_id", None) is FormatId.PLUGIN_SSE
        and getattr(slot.source_snapshot, "content", None) is None
    )


def requires_write_inputs(scope, output_format=None):
    slots = scope.slots
    return (output_format not in SST_FORMATS and bool(_unbound_plugins(slots))) or any(
        isinstance(slot.collection, ProjectRowsCollection) for slot in slots.values()
    )


def select_write_inputs(
    host, scope, resume, *, output_format=None, select_plugins=True, on_unavailable=None, is_current=None, parent=None
) -> None:
    unavailable = on_unavailable or (lambda message: host.show_message(message, error=True) if message else None)
    slots = scope.slots
    required = _unbound_plugins(slots) if select_plugins and output_format not in SST_FORMATS else ()
    _WriteInputs(host, scope, slots, required, resume, unavailable, is_current, parent).start()


class _WriteInputs:
    def __init__(self, host, scope, slots, required, resume, unavailable, is_current, parent):
        self.host = host
        self.parent = host if parent is None else parent
        self.scope = scope
        self.slots = slots
        self.missing = list(required)
        self.resume = resume
        self.unavailable = unavailable
        self.is_current = is_current
        self.signature = self._signature()
        use_cases = getattr(getattr(host, "app_runtime", None), "use_cases", None)
        names = () if use_cases is None or not required else use_cases.names()
        self.references = (
            use_cases.resolve("project_source_references") if "project_source_references" in names else None
        )
        self.projection = use_cases.resolve("project_projection") if "project_projection" in names else None
        self.token = None
        self.pending = None
        self.pending_namespaces = frozenset()
        self.snapshots = {}
        self.saved = {}
        self.repairs = {}
        self.rejected = {}
        self.directory = None
        self.encodings = scope.text_encodings

    def start(self):
        identity = self.signature[0]
        if identity is None:
            self.unavailable(_STALE)
            return
        self.request = replace(
            self.host.runtime_context,
            project_id=identity[0],
            variant_id=identity[1],
            run_id=f"plugin-write-input-{uuid4().hex}",
        )
        self._dispatch(self._prepare, "正在准备本次导出所需的来源文件…", self._next)

    def _signature(self):
        context = self.host.context
        return context.active_version_identity, context.project_revision, context.variant_revision

    def _stop_reason(self):
        if getattr(self.host, "close_pending", False) or (self.is_current is not None and not self.is_current()):
            return ""
        if self._signature() != self.signature or getattr(self.host.context, "project_opening", False):
            return _STALE
        if self.projection is not None:
            snapshot = self.projection.snapshot()
            values = {} if snapshot is None else snapshot.values
            signature = (
                (values.get("project_id"), values.get("variant_id")),
                values.get("project_revision"),
                values.get("variant_revision"),
            )
            if signature != self.signature:
                return _STALE
        return None

    def _check_current(self):
        reason = self._stop_reason()
        if reason is None:
            return True
        self.unavailable(reason or None)
        return False

    def _dispatch(self, operation, message, ready):
        if not self._check_current():
            return

        def received(_result):
            if self._check_current():
                ready()

        def failed(error):
            if self._check_current():
                self.unavailable(error)

        if not self.host.start_foreground_task(operation, message=message, on_result=received, on_error=failed):
            self.unavailable("另一项后台操作仍在进行，请稍候。")

    def _prepare(self):
        if self._stop_reason() is not None:
            return
        for key, slot in self.slots.items():
            rows = slot.collection
            self.snapshots[key] = (
                rows.native_template() if isinstance(rows, ProjectRowsCollection) else slot.source_snapshot
            )
        if not self.missing:
            return
        if self.encodings is None:
            self.encodings = context_text_encodings(self.host.context, self.request)
        if self.references is not None:
            self._read_references()
        preparer = TranslationIoProjectSourcePreparer()
        for key in tuple(self.missing):
            if self._stop_reason() is not None:
                return
            info = self.saved.get(key)
            path = info.reference_path if info is not None else getattr(self.slots[key], "reference_path", "")
            if path and Path(path).is_absolute():
                self._try_bind(key, path, preparer)

    def _read_references(self):
        project_id, variant_id = ProjectId(self.request.project_id), VariantId(self.request.variant_id)
        store = self.references.store_for(project_id)
        infos = {info.namespace: info for info in store.sources(native_fields=False)}
        if self.projection is not None:
            projection = self.projection.snapshot()
            if projection is None or self._stop_reason() is not None:
                return
            values = projection.values
            self.token = ProjectReadToken(
                project_id, variant_id, values["persisted_project_revision"], values["persisted_variant_revision"]
            )
            _, project_revision, variant_revision = self.signature
            stored_namespaces = {namespace.value for namespace in infos}
            missing_stored = any(
                (
                    dict(getattr(self.snapshots[key], "metadata", ())).get("source_namespace")
                    or self.slots[key].source_namespace
                )
                not in stored_namespaces
                for key in self.missing
            )
            if project_revision != self.token.project_revision or missing_stored:
                self.pending = self.references.capture_pending_sources(
                    project_id, variant_id, project_revision=project_revision, variant_revision=variant_revision
                )
                if self.pending is None:
                    raise RuntimeError(_STALE)
                pending_infos = {info.namespace: info for info in self.pending.sources}
                self.pending_namespaces = frozenset(pending_infos)
                infos.update(pending_infos)
        for key in self.missing:
            snapshot = self.snapshots[key]
            if snapshot is None:
                raise RuntimeError("当前来源缺少可校验的插件记录。")
            namespace = dict(snapshot.metadata).get("source_namespace") or self.slots[key].source_namespace
            info = infos.get(SourceNamespace(namespace)) if namespace else None
            if info is not None and info.fingerprint == snapshot.sha256:
                self.saved[key] = info

    def _next(self):
        if not self.missing:
            if self.repairs:
                self._dispatch(self._remember, "正在保存已验证的来源路径…", self._finish)
            else:
                self._finish()
            return
        key = self.missing[0]
        info = self.saved.get(key)
        title = f"为本次导出选择来源插件 · {self.slots[key].label}"
        if key in self.rejected:
            title += f" · {self.rejected[key]}"
        path, _ = QFileDialog.getOpenFileName(
            self.parent,
            title,
            str(self.directory) if self.directory is not None else info.reference_path if info is not None else "",
            "插件 (*.esp *.esm *.esl)",
        )
        if not path:
            self.unavailable(None)
            return
        self._dispatch(lambda: self._from_selection(key, path), "正在验证插件和配套 Strings…", self._next)

    def _from_selection(self, key, path):
        if self._stop_reason() is not None:
            return
        preparer = TranslationIoProjectSourcePreparer()
        self.directory = Path(path).parent
        if not self._try_bind(key, path, preparer, remember=True):
            return
        if not self.missing or self._stop_reason() is not None:
            return
        expected_names = {missing: self._source_name(missing).casefold() for missing in self.missing}
        expected_counts = Counter(expected_names.values())
        candidates = {}
        try:
            for candidate in self.directory.iterdir():
                name = candidate.name.casefold()
                if expected_counts[name] == 1:
                    candidates.setdefault(name, []).append(candidate)
        except OSError:
            _logger.debug("Cannot check sibling plugin files beside %s", path, exc_info=True)
            return
        for missing, name in expected_names.items():
            if self._stop_reason() is not None:
                return
            matches = candidates.get(name, ())
            if len(matches) == 1:
                self._try_bind(missing, str(matches[0]), preparer, remember=True)

    def _source_name(self, key):
        snapshot = self.snapshots[key]
        return snapshot.source.display_name or ""

    def _try_bind(self, key, path, preparer, *, remember=False):
        try:
            self._bind(key, path, preparer, remember=remember)
        except DomainError as exc:
            if exc.category not in _UNUSABLE_INPUT:
                raise
            _logger.debug("Plugin reference %s could not be bound: %s", path, exc)
            self.rejected[key] = exc.message
            return False
        except OSError:
            _logger.debug("Plugin reference %s is unreadable", path, exc_info=True)
            self.rejected[key] = "无法读取插件，请检查文件及读取权限。"
            return False
        return True

    def _bind(self, key, path, preparer, *, remember):
        expected = self.snapshots[key]
        if expected is None:
            raise RuntimeError("当前来源缺少可校验的插件记录。")
        entries = (entry.snapshot() for entry in self.slots[key].collection)
        chosen = bind_plugin_write_input(
            expected, entries, path, self.request, preparer, input_encoding=self.encodings.input_encoding
        )
        self.snapshots[key] = chosen
        self.missing.remove(key)
        info = self.saved.get(key)
        if remember and info is not None and chosen.source.uri != info.reference_path:
            self.repairs[key] = chosen.source.uri

    def _remember(self):
        for key, path in self.repairs.items():
            if self._stop_reason() is not None:
                return
            info = self.saved[key]
            source_id = self.slots[key].source_id or str(key)
            update = dict(expected_fingerprint=info.fingerprint, expected_path=info.reference_path, reference_path=path)
            if info.namespace in self.pending_namespaces:
                saved = self.references.update_pending_source_reference(
                    self.pending, source_id, info.namespace, **update
                )
            else:
                saved = self.references.update_source_reference(
                    ProjectId(self.request.project_id), source_id, info.namespace, token=self.token, **update
                )
            if not saved:
                raise RuntimeError("工程来源路径已变化，请重新发起导出。")

    def _finish(self):
        self.resume(
            replace(self.scope, snapshots={**self.scope.snapshots, **self.snapshots}, text_encodings=self.encodings)
        )
