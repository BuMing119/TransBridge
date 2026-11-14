# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Owned open-file dialog and worker, with immutable source publication."""

from dataclasses import dataclass, replace
import logging
from pathlib import Path

from PyQt6.QtCore import Qt

from transbridge.application.io.open_translation_file import prepare_open_file
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui.source_hydration import slot_from_hydration, source_slot_key
from transbridge.ui.text_encoding import context_text_encodings
from transbridge.ui.workers import ApiWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _OpenResult:
    slots: tuple
    failures: tuple[str, ...]
    authority: object


class OpenFileCoordinator:
    """Keep native format parsing out of the legacy ParseCoordinator and Qt thread."""

    def __init__(self, host, parser):
        self._host = host
        self._parser = parser
        self._dialogs = set()
        self._running = False

    def open(self):
        from transbridge.ui.workbench.open_file_dialog import OpenFileDialog

        if self._running:
            self._host.show_message("正在打开文件，请等待当前任务完成。")
            return None
        context = self._host.context
        authority = self._parser._capture_authoritative_target()
        dialog = OpenFileDialog(parent=self._host)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._dialogs.add(dialog)

        def submit():
            if self._running:
                self._host.show_message("正在打开文件，请等待当前任务完成。")
                return
            if not self._is_current(context, authority):
                self._host.show_message("工程版本或内容已变化，请重新打开文件窗口。", error=True)
                return
            self.start(dialog.get_config(), context=context, authority=authority)

        dialog.accepted.connect(submit)
        dialog.finished.connect(lambda _result: self._dialogs.discard(dialog))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _is_current(self, context, authority):
        return self._host.context is context and self._parser._can_publish_authoritative_source(authority)

    def start(self, options, *, context, authority):
        if self._running:
            return
        if not options.source_paths:
            self._host.show_message("请选择要打开的文件。", error=True)
            return
        self._running = True
        self._host.workbench.show_step2_progress(0, "正在打开翻译文件…")
        self._host.workbench.set_step2_parsing(True)
        runtime_context = context.runtime_context
        commands = context.project_commands

        def prepare():
            from .parse_coordinator import _apply_dictionary_to_collection

            read_options = context_text_encodings(context).read_options()
            slots, failures = [], []
            expected = authority
            seen = set()
            for path in options.source_paths:
                # Qt applies projection updates asynchronously. After our first commit,
                # its displayed revision may lag behind the authoritative revision.
                # Compare context identity here; the command checks all expected IDs
                # and revisions atomically before publishing each prepared source.
                if self._host.context is not context:
                    failures.append("工程版本或内容已变化，其余文件未打开。")
                    break
                try:
                    opened = prepare_open_file(
                        path,
                        options,
                        runtime_context,
                        apply_dictionary=_apply_dictionary_to_collection,
                        **read_options,
                    )
                    if self._host.context is not context:
                        raise ValueError("工程版本或内容已变化，文件未添加。")
                    hydration = opened.source.hydration
                    if authority is not None:
                        (project_id, variant_id), project_revision, variant_revision = expected
                        added = commands.add_prepared_source(
                            opened.source,
                            runtime_context,
                            expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
                            expected_project_revision=project_revision,
                            expected_variant_revision=variant_revision,
                        )
                        if not added.is_success or added.value is None:
                            raise ValueError("\n".join(f"{d.code}: {d.message}" for d in added.diagnostics))
                        value = added.value
                        expected = (project_id, variant_id), value.project_revision, value.variant_revision
                        hydration = replace(
                            value.hydration,
                            source_id=value.source_id,
                            reference_location=value.location or value.hydration.reference_location,
                        )
                    key = source_slot_key(hydration)
                    if key in seen or authority is None and key in context.slots:
                        raise ValueError("该翻译来源已打开，请使用导入译文或管理已有内容。")
                    slot = slot_from_hydration(hydration)
                    slot.strings_lookup = opened.strings_lookup
                    slot.strings_lang = options.strings_lang
                    slots.append((key, slot, opened.note))
                    seen.add(key)
                except Exception as exc:  # noqa: BLE001 - retain per-file failure and let independent files continue
                    logger.exception("Opening translation source failed: %s", path)
                    failures.append(f"{Path(path).name}：{exc}")
            return _OpenResult(tuple(slots), tuple(failures), expected)

        def done(result):
            if not self._is_current(context, result.authority):
                self._host.show_message(
                    "工程版本或内容已变化，未显示旧任务的文件；已登记来源保留在其原工程版本。", error=True
                )
                return
            for key, slot, _note in result.slots:
                context.add_slot(key, slot)
            notes = [note for _key, _slot, note in result.slots if note]
            message = "所选文件已打开。" if result.slots else "文件未打开。"
            if result.failures:
                message = "部分文件已打开，其余文件未添加。" if result.slots else "文件未打开。"
            self._host.show_message("\n".join((message, *notes, *result.failures)), error=bool(result.failures))

        def failed(message):
            self._host.show_message(f"打开文件失败：{message}", error=True)

        def finished():
            self._running = False
            self._host.workbench.hide_step2_progress()
            self._host.workbench.set_step2_parsing(False)

        worker = ApiWorker(prepare)
        worker.result.connect(done)
        worker.error.connect(failed)
        worker.finished.connect(finished)
        self._host.workers.append(worker)
        worker.start()
