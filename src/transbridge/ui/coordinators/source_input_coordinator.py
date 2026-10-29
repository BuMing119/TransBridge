"""Supply one missing operation input, then rebuild the requested operation."""

from dataclasses import replace
import logging
from uuid import uuid4

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QFileDialog, QInputDialog

from transbridge.application.io import FormatId
from transbridge.ui.source_input_state import missing_source_keys
from transbridge.ui.source_supplement_dialog import SourceSupplementDialog

logger = logging.getLogger(__name__)


class SourceInputCoordinator:
    def __init__(self, host):
        self._host = host
        self._busy = False
        self._replacements = {}
        self._entry_replacements = {}
        self._batch = None

    def resolved_entry_keys(self, keys):
        identity = self._host.context.active_version_identity
        result = []
        for key in keys:
            seen = set()
            while key is not None and key not in seen:
                seen.add(key)
                mapped = self._entry_replacements.get((identity, key), key)
                if mapped == key:
                    break
                key = mapped
            if key is not None:
                result.append(key)
        return tuple(dict.fromkeys(result))

    def entry_mappings(self):
        identity = self._host.context.active_version_identity
        return {
            old: next(iter(self.resolved_entry_keys((old,))), None)
            for (owner, old) in self._entry_replacements
            if owner == identity
        }

    def resolved_keys(self, keys):
        identity = self._host.context.active_version_identity
        resolved = []
        for key in keys:
            seen = set()
            while key not in seen:
                seen.add(key)
                replacement = self._replacements.get((identity, key), key)
                if replacement == key:
                    break
                key = replacement
            resolved.append(key)
        return tuple(dict.fromkeys(resolved))

    def open_ai(self, keys):
        self._host.tool_windows.open_ai_translator(source_keys=self.resolved_keys(keys))

    def ensure(
        self,
        requirement,
        source_keys=None,
        *,
        resume,
        parent=None,
        on_finished=None,
        is_current=None,
        on_scope_ready=None,
        allow_partial=False,
        operation_label="本次写回",
    ):
        if requirement != "template":
            return self._ensure_one(
                requirement,
                source_keys,
                resume=resume,
                parent=parent,
                on_finished=on_finished,
                is_current=is_current,
            )
        from .source_input_batch import SourceInputBatchCoordinator

        if self._batch is None:
            self._batch = SourceInputBatchCoordinator(self._host, self)
        return self._batch.ensure(
            source_keys,
            resume=resume,
            parent=parent,
            on_finished=on_finished,
            is_current=is_current,
            on_scope_ready=on_scope_ready,
            allow_partial=allow_partial,
            operation_label=operation_label,
        )

    def _ensure_one(
        self,
        requirement,
        source_keys=None,
        *,
        resume,
        parent=None,
        on_finished=None,
        is_current=None,
        selected_key=None,
        selected_path=None,
        auto_match=False,
        report_error=None,
    ):
        context = self._host.context
        source_keys = None if source_keys is None else self.resolved_keys(source_keys)
        missing = missing_source_keys(context, requirement, source_keys)
        if not missing:
            return True
        if self._busy:
            if on_finished is not None:
                on_finished(False)
            return False
        self._busy = True
        self._resume = resume
        self._on_finished = on_finished
        self._owner_current = is_current
        self._auto_match = auto_match
        self._report_error = report_error
        self._identity = context.active_version_identity
        self._request = replace(
            self._host.runtime_context,
            project_id=self._identity[0],
            variant_id=self._identity[1],
            run_id=f"source-input-{uuid4().hex}",
        )
        self._parent = parent or self._host
        if selected_key is None:
            labels = [
                f"{index + 1}. {context.slots[key].label} ({context.slots[key].source_namespace or key})"
                for index, key in enumerate(missing)
            ]
            selected, accepted = QInputDialog.getItem(
                self._parent, "补充输入", "本次操作需要以下来源的原文或文件模板。选择要补充的内容：", labels, 0, False
            )
            if not accepted:
                self._cancel()
                return False
            selected_key = missing[labels.index(selected)]
        slot = context.slots[selected_key]
        self._selected_key = selected_key
        self._source_id = getattr(slot, "source_id", None)
        self._namespace = getattr(slot, "source_namespace", None)
        format_id = getattr(slot, "format_id", None)
        if self._source_id is None:
            projection = self._host.app_runtime.use_cases.resolve("project_projection").snapshot()
            values = {} if projection is None else projection.values
            missing_ids = {item.get("source_id") for item in values.get("missing_inputs", ())}
            candidates = [source for source in values.get("sources", ()) if source.get("source_id") in missing_ids]
            if candidates:
                independent = "作为独立来源补充"
                labels = [
                    f"{source.get('display_name') or '工程来源'} [{source['source_id']}]" for source in candidates
                ]
                choice, accepted = QInputDialog.getItem(
                    self._parent,
                    "选择译文所属来源",
                    "该组保存译文属于哪个工程来源？",
                    [*labels, independent],
                    0,
                    False,
                )
                if not accepted:
                    self._cancel()
                    return False
                if choice != independent:
                    source = candidates[labels.index(choice)]
                    self._source_id = source["source_id"]
                    if source.get("format_id"):
                        format_id = FormatId(source["format_id"])
        if format_id is None:
            formats = (
                ("ESP / ESM / ESL 插件", FormatId.PLUGIN_SSE),
                ("XTranslator XML", FormatId.XML_XT),
                ("ESP-ESM Translator XML", FormatId.XML_EET),
                ("ESP-ESM Translator EET", FormatId.BINARY_EET),
                ("ParaTranz JSON", FormatId.JSON_PARATRANZ),
                ("SST SSU8", FormatId.SST_SSU8),
                ("SST SSU9", FormatId.SST_SSU9),
            )
            choice, accepted = QInputDialog.getItem(
                self._parent,
                "选择输入格式",
                "保存记录没有确定输入格式，请选择本次文件的格式：",
                [label for label, _ in formats],
                0,
                False,
            )
            if not accepted:
                self._cancel()
                return False
            format_id = dict(formats)[choice]
        path = selected_path
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self._parent, "选择本次操作需要的源文件或模板", "", "来源文件 (*)")
        if not path:
            self._cancel()
            return False
        self._format = format_id
        self._path = path
        self._project_path = self._service().active_project_path(self._request)

        def prepare(saved):
            if not saved or not self._current():
                self._cancel()
                return
            self._dispatch(
                lambda: self._service().prepare(
                    self._project_path,
                    self._source_id,
                    self._namespace,
                    self._path,
                    self._request,
                    format_id=self._format,
                ),
                self._preview,
            )

        self._host.save_current_project_async(on_finished=prepare)
        return False

    def _service(self):
        return self._host.app_runtime.use_cases.resolve("project_source_supplements")

    def _current(self):
        return (
            self._busy
            and not getattr(self._host, "close_pending", False)
            and self._host.context.active_version_identity == self._identity
            and (self._owner_current is None or self._owner_current())
        )

    def _dispatch(self, operation, callback):
        started = self._host.start_foreground_task(
            operation,
            message="正在补充处理所需的输入…",
            on_result=lambda result: QTimer.singleShot(0, lambda: callback(result)),
            on_error=self._error,
        )
        if not started:
            self._cancel()

    def _preview(self, result):
        self._token = getattr(result.value, "token", None)
        if not self._check(result):
            return
        preview = result.value
        if self._auto_match and preview.rows and preview.unmatched_count == 0:
            self._apply_mapping(preview, {row.old_key: row.matched_key for row in preview.rows})
            return
        try:
            dialog = SourceSupplementDialog(preview, self._parent)
            try:
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
                mappings = dialog.mappings()
            finally:
                dialog.deleteLater()
        except Exception as exc:
            logger.exception("Could not display source input correspondence")
            self._error(f"输入对应预览未能完成：{exc}。请重新发起本次操作。")
            return
        if not accepted or not self._current():
            self._cancel()
            return
        self._apply_mapping(preview, mappings)

    def _apply_mapping(self, preview, mappings):
        self._next_namespace = next(
            (key.namespace.value for key in mappings.values() if key is not None),
            preview.choices[0].key.namespace.value if preview.choices else None,
        )
        self._mappings = mappings

        def commit(remapped):
            if self._check(remapped):
                self._dispatch(lambda: self._service().commit(remapped.value.token, self._request), self._committed)

        self._dispatch(lambda: self._service().remap(preview.token, mappings, self._request), commit)

    def _committed(self, result):
        if not self._check(result):
            return

        def reopen():
            if not self._current():
                self._cancel()
                return
            self._host.project_coordinator.open_project_identity(self._request, self._reopened)

        QTimer.singleShot(0, reopen)

    def _reopened(self, success):
        if not self._current():
            self._cancel()
            return
        resume, namespace = self._resume, self._next_namespace
        if success:
            identity = self._host.context.active_version_identity
            for old, new in self._mappings.items():
                self._entry_replacements[(identity, old)] = new
        if not success:
            self._cancel()
            return
        if namespace:
            target = next(
                (
                    key
                    for key, slot in self._host.context.slots.items()
                    if getattr(slot, "source_namespace", None) == namespace
                ),
                None,
            )
            if target:
                self._replacements[(self._host.context.active_version_identity, self._selected_key)] = target
                self._host.context.activate_slot(target)
        self._cancel(completed=True)
        QTimer.singleShot(0, resume)

    def _check(self, result):
        if not self._current():
            self._cancel()
            return False
        if result.is_success and result.value is not None:
            return True
        self._error("\n".join(f"{item.code}: {item.message}" for item in result.diagnostics))
        return False

    def _error(self, message):
        report = getattr(self, "_report_error", None)
        self._cancel()
        if report is not None:
            report(message)
        else:
            self._host.show_message(message, error=True)

    def _cancel(self, *, completed=False):
        self._busy = False
        self._resume = None
        token = getattr(self, "_token", None)
        self._token = None
        if token is not None:
            self._service().discard(token, self._request)
        finished = getattr(self, "_on_finished", None)
        self._on_finished = None
        self._owner_current = None
        self._parent = None
        if finished is not None:
            finished(completed)
