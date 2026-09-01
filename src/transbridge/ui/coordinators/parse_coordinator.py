from dataclasses import replace
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from ..context import CollectionSlot
from ..workers import ApiWorker


def _apply_dictionary_to_collection(collection):
    """解析后自动套用词典，将已有译文填入未翻译条目。

    自动套用走「全词典兜底」（mod_file_id 为空，跳过同名 mod，其余 project/global 全查）。
    只填空译文（不覆盖已有译文）；词典为空或加载失败时静默返回 0，不阻断解析。

    :return: 命中填充的条目数（用于累加到 migrate_count）
    """
    try:
        from transbridge.translation_memory import TranslationMemoryManager
        from transbridge.translation_memory.manager import QueryContext

        manager = TranslationMemoryManager()
        manager.load()

        context = QueryContext(mod_file_id="")
        result = manager.apply_to_collection(collection, context=context)
        return result.applied
    except Exception:  # noqa: BLE001 - 词典不可用时不影响解析
        return 0


class ParseCoordinator:
    """Own one application-shell interaction slice."""

    def __init__(self, host) -> None:
        self._host = host
        self._owned_dialogs: set[object] = set()

    def parse_plugin(self):
        """弹出解析配置对话框，执行后台解析。"""
        from ..workbench._parse_config_dialog import ParseConfigDialog

        dlg = ParseConfigDialog(mode="parse", parent=self._host)
        if dlg.exec() != ParseConfigDialog.DialogCode.Accepted:
            return

        cfg = dlg.get_config()

        if cfg.source_mode == "eet":
            if not cfg.eet_path:
                self._host.show_message("请先选择 EET XML 文件")
                return
            self._run_parse_eet(cfg)
        else:
            if not cfg.esp_paths:
                self._host.show_message("请先选择插件文件")
                return
            if len(cfg.esp_paths) > 1:
                self._run_batch_parse_esp(cfg)
            else:
                self._run_parse_esp(cfg)

    def apply_migration(
        self,
        source_path: str | None = None,
        drop_kind: str | None = None,
        format_id: str | None = None,
    ):
        """Open a non-blocking migration draft for the current collection."""
        slot = self._host.context.active_slot
        if not slot:
            self._host.show_message("请先加载集合")
            return

        from ..workbench._parse_config_dialog import ParseConfigDialog

        dlg = ParseConfigDialog(mode="migrate", parent=self._host)
        if source_path is not None and not dlg.prefill_migration_source(source_path, drop_kind or "", format_id):
            self._host.show_message("DROP_MIGRATION_ADAPTER_UNAVAILABLE: 已识别该来源，但当前工程尚无对应迁移适配器。")
            return

        def submit() -> None:
            cfg = dlg.get_config()
            json_path = getattr(cfg, "json_path", None)
            sst_path = getattr(cfg, "sst_path", None)
            if not any([cfg.eet_path, cfg.xt_path, cfg.tp_path, cfg.strings_dir, json_path, sst_path]):
                self._host.show_message("请先选择迁移源文件")
                return
            if (json_path or sst_path) and any([cfg.eet_path, cfg.xt_path, cfg.tp_path, cfg.strings_dir]):
                self._host.show_message(
                    "MIGRATION_MIXED_LEGACY_UNSUPPORTED: JSON/SST 原子导入不能与旧迁移来源在同一次草稿中混用。"
                )
                return
            self._run_migrate(slot, cfg)

        self._owned_dialogs.add(dlg)
        dlg.setModal(False)
        dlg.accepted.connect(submit)
        dlg.finished.connect(lambda _result, owned=dlg: self._owned_dialogs.discard(owned))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _run_parse_esp(self, cfg):
        from transbridge.parser.plugin_parser import PluginParser
        from transbridge.parser.strings_file import PluginStringsLookup
        from transbridge.parser.xt import XT_XmlParser

        esp_path = cfg.esp_paths[0]
        authority = self._capture_authoritative_target()
        publication = [authority]
        self._host.workbench.show_step2_progress(0, "解析中…")
        self._host.workbench.set_step2_parsing(True)

        def _do():
            parser = PluginParser()
            entries = parser.parse_plugin(Path(esp_path), skip_empty=cfg.skip_empty)
            collection = TranslationEntryCollection(entries)
            migrate_count = 0
            if cfg.eet_path:
                try:
                    migrate_count += collection.update_from_eet_xml(Path(cfg.eet_path))
                except Exception:
                    pass
            if cfg.xt_path:
                try:
                    xp = XT_XmlParser.from_file(cfg.xt_path)
                    migrate_count += collection.apply_xt_entries(xp.entries)
                except Exception:
                    pass
            if cfg.tp_path:
                try:
                    migrate_count += collection.update_from_translated_plugin(Path(cfg.tp_path))
                except Exception:
                    pass
            if cfg.strings_dir:
                try:
                    plugin_stem = Path(esp_path).stem
                    strings_lookup = PluginStringsLookup.from_strings_dir(
                        Path(cfg.strings_dir), plugin_stem, cfg.strings_lang
                    )
                    if strings_lookup:
                        migrate_count += collection.update_from_strings_lookup(strings_lookup)
                except Exception:
                    pass
            # 自动套用词典（全局词典兜底，填空译文）
            migrate_count += _apply_dictionary_to_collection(collection)
            collection, hydration = self._commit_authoritative_source(
                esp_path,
                collection,
                format_id="plugin.sse",
                options=(("skip_empty", cfg.skip_empty),),
                expected_authority=authority,
                committed_authority=publication,
            )
            return collection, migrate_count, parser.get_plugin(), parser.get_strings_lookup(), hydration

        def _on_done(result):
            collection, migrate_count, plugin, strings_lookup, hydration = result
            self._host.workbench.hide_step2_progress()
            self._host.workbench.set_step2_parsing(False)
            label = Path(esp_path).stem
            slot = CollectionSlot(
                label=label,
                collection=collection,
                esp_path=esp_path,
                eet_path=cfg.eet_path,
                xt_path=cfg.xt_path,
                strings_path=cfg.strings_dir,
                strings_lang=cfg.strings_lang,
                migrate_count=migrate_count,
                plugin=plugin,
                strings_lookup=strings_lookup,
                source_snapshot=None if hydration is None else hydration.source_snapshot,
                format_id=None if hydration is None else hydration.format_id,
            )
            self._finish_parse(esp_path, slot, collection, expected_authority=publication[0])

        def _on_error(msg: str):
            self._host.workbench.hide_step2_progress()
            self._host.workbench.set_step2_parsing(False)
            self._host.show_message(f"解析失败：{msg}")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._host.workers.append(w)

    def _run_batch_parse_esp(self, cfg):
        from transbridge.parser.plugin_parser import PluginParser

        esp_paths = cfg.esp_paths
        total = len(esp_paths)
        self._host.workbench.show_step2_progress(total, f"批量解析中 (0/{total})…")
        self._host.workbench.set_step2_parsing(True)

        results = []
        current = [0]
        batch_identity = self._host.context.active_version_identity
        publication = [self._capture_authoritative_target(expected_identity=batch_identity)]

        def _parse_next():
            if current[0] >= total:
                _finish_batch()
                return
            esp_path = esp_paths[current[0]]
            try:
                if current[0] and not self._can_publish_authoritative_source(publication[0]):
                    raise RuntimeError("解析期间活动工程版本或修订已变化。")
                authority = self._capture_authoritative_target(expected_identity=batch_identity)
            except RuntimeError:
                results.extend((path, None, None) for path in esp_paths[current[0] :])
                _finish_batch()
                return
            self._host.workbench.update_step2_progress(current[0], total, f"批量解析中 ({current[0] + 1}/{total})…")

            def _do():
                parser = PluginParser()
                entries = parser.parse_plugin(Path(esp_path), skip_empty=cfg.skip_empty)
                collection = TranslationEntryCollection(entries)
                # 自动套用词典（全局词典兜底，填空译文）
                dict_hits = _apply_dictionary_to_collection(collection)
                collection, hydration = self._commit_authoritative_source(
                    esp_path,
                    collection,
                    format_id="plugin.sse",
                    options=(("skip_empty", cfg.skip_empty),),
                    expected_authority=authority,
                    committed_authority=publication,
                )
                return collection, dict_hits, parser.get_plugin(), parser.get_strings_lookup(), hydration

            def _on_one_done(result):
                collection, migrate_count, plugin, strings_lookup, hydration = result
                label = Path(esp_path).stem
                slot = CollectionSlot(
                    label=label,
                    collection=collection,
                    esp_path=esp_path,
                    eet_path=None,
                    xt_path=None,
                    strings_path=None,
                    strings_lang=cfg.strings_lang,
                    migrate_count=migrate_count,
                    plugin=plugin,
                    strings_lookup=strings_lookup,
                    source_snapshot=None if hydration is None else hydration.source_snapshot,
                    format_id=None if hydration is None else hydration.format_id,
                )
                results.append((esp_path, slot, collection))
                current[0] += 1
                _parse_next()

            def _on_one_error(msg: str):
                results.append((esp_paths[current[0]], None, None))
                current[0] += 1
                _parse_next()

            w = ApiWorker(_do)
            w.result.connect(_on_one_done)
            w.error.connect(_on_one_error)
            w.start()
            self._host.workers.append(w)

        def _finish_batch():
            self._host.workbench.hide_step2_progress()
            self._host.workbench.set_step2_parsing(False)
            if not self._can_publish_authoritative_source(publication[0]):
                self._host.show_message("解析期间活动工程版本或修订已变化，未发布过期集合，请重新加载当前版本。")
                return
            success_count = sum(1 for _, slot, _ in results if slot is not None)
            fail_count = total - success_count
            for esp_path, slot, collection in results:
                if slot:
                    self._host.context.add_slot(esp_path, slot)
                    self._save_source_to_project(slot)
            if results:
                for esp_path, slot, _ in reversed(results):
                    if slot:
                        self._host.context.activate_slot(esp_path)
                        break
            msg = f"批量解析完成：成功 {success_count} 个"
            if fail_count > 0:
                msg += f"，失败 {fail_count} 个"
            self._host.show_message(msg)

        _parse_next()

    def _run_parse_eet(self, cfg):
        from transbridge.parser.xt import XT_XmlParser

        eet_path = cfg.eet_path
        authority = self._capture_authoritative_target()
        publication = [authority]
        self._host.workbench.show_step2_progress(0, "解析 EET 中…")
        self._host.workbench.set_step2_parsing(True)

        def _do():
            collection = TranslationEntryCollection.from_eet_xml(Path(eet_path))
            migrate_count = 0
            if cfg.xt_path:
                try:
                    xp = XT_XmlParser.from_file(cfg.xt_path)
                    migrate_count += collection.apply_xt_entries(xp.entries)
                except Exception:
                    pass
            if cfg.tp_path:
                try:
                    migrate_count += collection.update_from_translated_plugin(Path(cfg.tp_path))
                except Exception:
                    pass
            collection, hydration = self._commit_authoritative_source(
                eet_path,
                collection,
                format_id="xml.eet",
                expected_authority=authority,
                committed_authority=publication,
            )
            return collection, migrate_count, hydration

        def _on_done(result):
            collection, migrate_count, hydration = result
            self._host.workbench.hide_step2_progress()
            self._host.workbench.set_step2_parsing(False)
            label = Path(eet_path).stem
            slot = CollectionSlot(
                label=label,
                collection=collection,
                eet_path=eet_path,
                xt_path=cfg.xt_path,
                migrate_count=migrate_count,
                source_snapshot=None if hydration is None else hydration.source_snapshot,
                format_id=None if hydration is None else hydration.format_id,
            )
            self._finish_parse(eet_path, slot, collection, expected_authority=publication[0])

        def _on_error(msg: str):
            self._host.workbench.hide_step2_progress()
            self._host.workbench.set_step2_parsing(False)
            self._host.show_message(f"解析失败：{msg}")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._host.workers.append(w)

    def _finish_parse(self, key: str, slot: CollectionSlot, collection, *, expected_authority=None):
        if not self._can_publish_authoritative_source(expected_authority):
            self._host.show_message("解析期间活动工程版本或修订已变化，未发布过期集合，请重新加载当前版本。")
            return
        if key in self._host.context.slots:
            ret = QMessageBox.question(
                self._host,
                "集合已存在",
                f"集合「{slot.label}」已存在，是否覆盖？\n选择「否」将保留原有集合。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                self._host.show_message("已取消，保留原有集合")
                return
            # The modal confirmation can process a queued Variant change.
            if not self._can_publish_authoritative_source(expected_authority):
                self._host.show_message("确认期间活动工程版本或修订已变化，未发布过期集合。")
                return
        self._host.context.add_slot(key, slot)
        self._save_source_to_project(slot)
        self._host.show_message(f"解析完成，共 {len(collection)} 条词条")

    def _save_source_to_project(self, slot: CollectionSlot) -> None:
        """将解析的源文件路径保存到 project.json（下次启动自动恢复集合）。"""
        if self._host.context.uses_authoritative_projection:
            return
        proj = self._host.context.active_project
        if proj is None:
            return
        if slot.esp_path and not any(s.get("key") == slot.esp_path for s in proj.sources):
            proj.add_source(slot.esp_path, "esp", slot.esp_path)
        if slot.eet_path and not any(s.get("key") == slot.eet_path for s in proj.sources):
            proj.add_source(slot.eet_path, "eet", slot.eet_path)
        if slot.xt_path and not any(s.get("key") == slot.xt_path for s in proj.sources):
            proj.add_source(slot.xt_path, "xt", slot.xt_path)
        if slot.sst_path and not any(s.get("key") == slot.sst_path for s in proj.sources):
            proj.add_source(slot.sst_path, "sst", slot.sst_path)
        proj.save()

    def _commit_authoritative_source(
        self,
        path: str,
        collection: TranslationEntryCollection,
        *,
        format_id: str,
        options=(),
        expected_authority=None,
        committed_authority=None,
    ) -> tuple[TranslationEntryCollection, object | None]:
        """Commit a parsed source before exposing its workbench projection."""

        context = self._host.context
        if not context.uses_authoritative_projection:
            return collection, None
        from transbridge.application.io import FormatId
        from transbridge.application.projects import ProjectSourceRequest
        from transbridge.application.projects.source_commands import source_request_with_initial_entry_states
        from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
        from transbridge.ui.source_hydration import collection_from_hydration

        if expected_authority is None:
            raise RuntimeError("ACTIVE_VARIANT_REQUIRED: 解析任务没有绑定活动工程版本。")
        identity, project_revision, variant_revision = expected_authority
        project_id, variant_id = identity

        request = source_request_with_initial_entry_states(
            ProjectSourceRequest(path, FormatId(format_id), options=tuple(options)),
            {entry.identity.local_key: (entry.translation, entry.stage) for entry in collection},
        )
        added = context.project_commands.add_source(
            request,
            context.runtime_context,
            expected_project_revision=project_revision,
            expected_variant_revision=variant_revision,
            expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
        )
        if not added.is_success or added.value is None:
            diagnostic = added.diagnostics[0]
            raise RuntimeError(f"{diagnostic.code}: {diagnostic.message}")
        if added.value.hydration is None:
            raise RuntimeError("PROJECT_SOURCE_HYDRATION_REQUIRED: 工程来源没有可用的界面读取模型。")
        if committed_authority is not None:
            committed_authority[0] = identity, added.value.project_revision, added.value.variant_revision

        return collection_from_hydration(added.value.hydration), added.value.hydration

    def _can_publish_authoritative_source(self, expected_authority) -> bool:
        context = self._host.context
        if expected_authority is None:
            return not context.uses_authoritative_projection
        return expected_authority == (
            context.active_version_identity,
            context.project_revision,
            context.variant_revision,
        )

    def _capture_authoritative_target(self, *, expected_identity=None):
        context = self._host.context
        if not context.uses_authoritative_projection:
            return None
        identity = context.active_version_identity
        if identity is None:
            raise RuntimeError("请先打开一个工程版本。")
        if expected_identity is not None and identity != expected_identity:
            raise RuntimeError("解析期间活动工程版本已变化。")
        return identity, context.project_revision, context.variant_revision

    def _run_migrate(self, slot, cfg):
        if getattr(cfg, "json_path", None) or getattr(cfg, "sst_path", None):
            self._run_structured_migrate(slot, cfg)
            return

        from transbridge.parser.strings_file import PluginStringsLookup
        from transbridge.parser.xt import XT_XmlParser

        self._host.workbench.show_step2_progress(0, "应用迁移源中…")

        def _do():
            migrate_count = 0
            updated_slots = []
            apply_all = cfg.strings_apply_all and cfg.strings_dir
            slots_to_process = list(self._host.context.slots.values()) if apply_all else [slot]

            for s in slots_to_process:
                collection = s.collection
                slot_migrate = 0
                if s is slot:
                    if cfg.eet_path and s.eet_path is None:
                        try:
                            slot_migrate += collection.update_from_eet_xml(Path(cfg.eet_path))
                        except Exception:
                            pass
                    if cfg.xt_path and s.xt_path is None:
                        try:
                            xp = XT_XmlParser.from_file(cfg.xt_path)
                            slot_migrate += collection.apply_xt_entries(xp.entries)
                        except Exception:
                            pass
                    if cfg.tp_path:
                        try:
                            slot_migrate += collection.update_from_translated_plugin(Path(cfg.tp_path))
                        except Exception:
                            pass
                if cfg.strings_dir and s.strings_path is None:
                    try:
                        plugin_stem = Path(s.esp_path).stem if s.esp_path else ""
                        strings_lookup = PluginStringsLookup.from_strings_dir(
                            Path(cfg.strings_dir), plugin_stem, cfg.strings_lang
                        )
                        if strings_lookup:
                            slot_migrate += collection.update_from_strings_lookup(strings_lookup)
                            s.strings_lookup = strings_lookup
                    except Exception:
                        pass
                if slot_migrate > 0:
                    updated_slots.append((s, slot_migrate))
                migrate_count += slot_migrate
            return migrate_count, cfg.eet_path, cfg.xt_path, cfg.strings_dir, cfg.strings_lang, updated_slots

        def _on_done(result):
            migrate_count, new_eet, new_xt, new_strings, new_lang, updated_slots = result
            if self._host.context.uses_authoritative_projection and updated_slots:
                states = {
                    entry.identity: (entry.translation, entry.stage)
                    for updated_slot, _count in updated_slots
                    for entry in updated_slot.collection
                }
                committed = self._host.context.project_commands.replace_entry_states(
                    states,
                    self._host.context.runtime_context,
                )
                if not committed.is_success:
                    diagnostic = committed.diagnostics[0]
                    self._host.workbench.hide_step2_progress()
                    self._host.show_message(f"{diagnostic.code}: {diagnostic.message}")
                    return
            for s, _ in updated_slots:
                if s is slot:
                    if new_eet and s.eet_path is None:
                        s.eet_path = new_eet
                    if new_xt and s.xt_path is None:
                        s.xt_path = new_xt
                if new_strings and s.strings_path is None:
                    s.strings_path = new_strings
                    s.strings_lang = new_lang
            self._host.workbench.hide_step2_progress()
            if cfg.strings_apply_all and len(updated_slots) > 1:
                self._host.show_message(f"迁移完成，共 {len(updated_slots)} 个集合，新增 {migrate_count} 条译文")
            else:
                self._host.show_message(f"迁移完成，新增 {migrate_count} 条译文")
            self._host.context.collection_changed.emit(slot.collection)

        def _on_error(msg: str):
            self._host.workbench.hide_step2_progress()
            self._host.show_message(f"迁移失败：{msg}")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._host.workers.append(w)

    def _run_structured_migrate(self, slot, cfg) -> None:
        """Prepare JSON/SST changes off-thread, then publish one Variant mutation."""

        from transbridge.application.io.migration_import import MigrationImportError, prepare_migration_import
        from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef

        context = self._host.context
        authority = self._capture_authoritative_target()
        original_collection = slot.collection
        sources = tuple(
            item
            for item in (
                (getattr(cfg, "json_path", None), getattr(cfg, "json_format_id", None)),
                (getattr(cfg, "sst_path", None), getattr(cfg, "sst_format_id", None)),
            )
            if item[0]
        )
        self._host.workbench.show_step2_progress(0, "验证迁移源中…")

        def _do():
            proposals = {}
            formats = []
            skipped = 0
            for path, format_hint in sources:
                draft = prepare_migration_import(
                    path,
                    original_collection,
                    format_hint=format_hint,
                    context=context.runtime_context,
                )
                formats.append(draft.format_id)
                skipped += draft.skipped_unmatched
                for entry_key, state in draft.states:
                    previous = proposals.get(entry_key)
                    if previous is not None and previous != state:
                        raise MigrationImportError(
                            "MIGRATION_ENTRY_KEY_CONFLICT",
                            f"多个迁移源为同一 EntryKey 提供了不同译文：{entry_key.local_key}",
                        )
                    proposals[entry_key] = state

            changed_states = {}
            staged_entries = []
            for entry in original_collection:
                proposed = proposals.get(entry.identity)
                if proposed is None or entry.translation or entry.stage != 0:
                    staged_entries.append(entry)
                    continue
                translation, stage = proposed
                changed_states[entry.identity] = proposed
                staged_entries.append(
                    replace(
                        entry,
                        translation=translation,
                        stage=stage,
                        revision=entry.revision.next(),
                    )
                )
            return (
                changed_states,
                TranslationEntryCollection(staged_entries),
                tuple(formats),
                skipped,
            )

        def _on_done(result):
            changed_states, candidate, formats, skipped = result
            if slot.collection is not original_collection:
                self._host.workbench.hide_step2_progress()
                self._host.show_message("MIGRATION_TARGET_CHANGED: 导入期间当前词条集合已变化，草稿未提交。")
                return
            if context.uses_authoritative_projection and changed_states:
                if authority is None:
                    self._host.workbench.hide_step2_progress()
                    self._host.show_message("ACTIVE_VARIANT_REQUIRED: 导入草稿没有绑定活动工程版本。")
                    return
                identity, project_revision, variant_revision = authority
                project_id, variant_id = identity
                committed = context.project_commands.replace_entry_states(
                    changed_states,
                    context.runtime_context,
                    expected_project_revision=project_revision,
                    expected_variant_revision=variant_revision,
                    expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
                )
                if not committed.is_success:
                    diagnostic = committed.diagnostics[0]
                    self._host.workbench.hide_step2_progress()
                    self._host.show_message(f"{diagnostic.code}: {diagnostic.message}")
                    return
            elif context.uses_authoritative_projection and not self._can_publish_authoritative_source(authority):
                self._host.workbench.hide_step2_progress()
                self._host.show_message("MIGRATION_TARGET_CHANGED: 活动工程版本已变化，草稿未提交。")
                return

            slot.collection = candidate
            if getattr(cfg, "sst_path", None) and changed_states:
                slot.sst_path = cfg.sst_path
            self._host.workbench.hide_step2_progress()
            suffix = f"；{skipped} 条无法唯一匹配已跳过" if skipped else ""
            formats_label = " + ".join(item.value for item in formats)
            self._host.show_message(f"迁移完成（{formats_label}），新增 {len(changed_states)} 条译文{suffix}")
            context.collection_changed.emit(slot.collection)

        def _on_error(msg: str):
            self._host.workbench.hide_step2_progress()
            self._host.show_message(f"迁移失败：{msg}")

        worker = ApiWorker(_do)
        worker.result.connect(_on_done)
        worker.error.connect(_on_error)
        worker.start()
        self._host.workers.append(worker)
