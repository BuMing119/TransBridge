from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMessageBox,
)

from transbridge.parser.eet_parser import EET_XmlParser
from transbridge.parser.xt import XT_XmlParser
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.writer.eet_xml_writer import EETWriter
from transbridge.writer.plugin_writer import PluginWriter
from transbridge.writer.xt_xml_writer import XTWriter

from .base import OpCard
from .presenter import OperationCardPresenter
from .write_views import (
    BatchConfirmDialog as _BatchConfirmDialog,
    BatchResultDialog as _BatchResultDialog,
    SlotSelectDialog as _SlotSelectDialog,
    WriteTargetDialog as _WriteTargetDialog,
)


@dataclass
class BatchWriteResult:
    """批量写回结果汇总。"""

    success_count: int = 0
    failed_count: int = 0
    total_entries: int = 0
    details: list[str] = field(default_factory=list)


class WriteCard(OpCard):
    def __init__(self, ctx, run_worker, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(
            "写回插件/XML",
            "将集合中的译文写回 ESP 插件或 EET/XT XML 文件。",
            "写回",
            parent,
            theme_view=theme_view,
        )
        self._output_dir_override: Path | None = None  # S08: 全版本写回时覆盖输出目录
        self._ctx = ctx
        self._presenter = OperationCardPresenter(ctx)
        self._run_worker = run_worker
        self.btn.clicked.connect(self.write)
        self.batch_btn.clicked.connect(self.batch_write)
        # eet_btn / xt_btn 作为 step3 按钮状态管理的占位符，与 self.btn 相同
        self.eet_btn = self.btn
        self.xt_btn = self.btn

    def update_batch_visibility(self):
        """更新批量按钮可见性（由 step3 调用）。"""
        self.set_batch_visible(self._presenter.batch_available)

    def batch_write(self):
        """批量写回入口。"""
        if self._dispatch_planned("write", self._ctx, batch=True):
            return
        slots = self._ctx.slots
        if len(slots) <= 1:
            return

        # 弹出插件选择对话框
        dlg = _SlotSelectDialog("批量写回 - 选择插件", slots, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_keys = dlg.selected_slots()
        if not selected_keys:
            QMessageBox.warning(self, "未选择插件", "请至少选择一个插件进行批量写回。")
            return

        selected_slots = [slots[k] for k in selected_keys]

        # 过滤出有 plugin 实例的槽位
        valid_slots = [s for s in selected_slots if s.plugin is not None]
        if not valid_slots:
            QMessageBox.warning(self, "无可写回插件", "所选插件均无 plugin 实例，无法写回。")
            return

        self.do_batch_write(valid_slots)

    def do_batch_write(self, selected_slots: list):
        """执行批量写回（由 step3 调用）。"""
        slot_names = [s.label or Path(s.esp_path).stem for s in selected_slots]

        # 使用可滚动确认对话框
        items = [f"• {name}" for name in slot_names]
        header = f"即将写回 {len(selected_slots)} 个插件\n确认后将选择输出目录。"

        dlg = _BatchConfirmDialog("确认批量写回", header, items, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # 选择输出目录
        output_dir = QFileDialog.getExistingDirectory(self, "选择批量写回输出目录")
        if not output_dir:
            return
        output_dir = Path(output_dir)

        def _batch_write():
            results = BatchWriteResult()

            for i, slot in enumerate(selected_slots):
                slot_name = slot.label or Path(slot.esp_path).stem

                try:
                    writer = PluginWriter(
                        slot.plugin,
                        strings_lookup=slot.strings_lookup,
                        language=slot.strings_lang,
                    )
                    count = writer.apply_collection(slot.collection)

                    # 确定输出路径
                    if slot.esp_path:
                        esp_output_path = output_dir / Path(slot.esp_path).name
                    else:
                        esp_output_path = output_dir / f"{slot_name}.esp"

                    write_result = writer.write(esp_output_path)
                    results.success_count += 1
                    results.total_entries += count

                    # 构建详情信息
                    strings_written = write_result.get("strings_written", [])
                    if strings_written:
                        strings_info = f", strings: {len(strings_written)} 个文件"
                    else:
                        strings_info = ""
                    results.details.append(f"✓ {slot_name}: {count} 条{strings_info}")

                except Exception as e:
                    results.failed_count += 1
                    results.details.append(f"✗ {slot_name}: {e}")

            return results

        def _on_done(result: BatchWriteResult):
            header = (
                f"成功：{result.success_count} 个\n"
                f"失败：{result.failed_count} 个\n"
                f"写入词条总数：{result.total_entries} 条\n"
                f"输出目录：{output_dir}"
            )
            dlg = _BatchResultDialog("批量写回完成", header, result.details, parent=self)
            dlg.exec()

        self._run_worker(
            _batch_write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "批量写回失败", e),
            progress_total=0,
            progress_msg="正在批量写回…",
        )

    def write(self):
        if self._dispatch_planned("write", self._ctx):
            return
        collection = self._ctx.collection
        if not collection:
            return

        dlg = _WriteTargetDialog(
            self._ctx.eet_path,
            self._ctx.xt_path,
            has_esp=(
                self._ctx.plugin is not None
                or (
                    self._ctx.active_slot is not None
                    and self._ctx.active_slot.source_snapshot is not None
                    and getattr(self._ctx.active_slot.format_id, "value", None) == "plugin.sse"
                )
            ),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # 检查是否有多个版本需要分版本写回
        proj = self._ctx.active_project
        variants = proj.variants if proj else []
        if len(variants) > 1:
            ret = QMessageBox.question(
                self,
                "写回模式",
                f"当前项目有 {len(variants)} 个版本。\n\n"
                "「是」——分别写回所有版本到独立子目录\n"
                "「否」——仅写回当前版本「{self._ctx.active_variant}」",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            write_all = ret == QMessageBox.StandardButton.Yes
        else:
            write_all = False

        if write_all:
            self._write_all_variants(dlg.target, dlg)
        else:
            self._write_single(dlg.target, dlg, collection)

    def _write_single(self, target, dlg, collection):
        """写回单个版本（原有逻辑）。"""
        if target == "esp":
            self._write_esp(collection)
        elif target == "eet":
            self._write_eet(collection, dlg.eet_path)
        elif target == "xt":
            self._write_xt(collection, dlg.xt_path)
        else:
            self._write_dsd(collection)

    def _write_all_variants(self, target, dlg):
        """遍历所有版本，分别写回到 {output_dir}/{variant_name}/。"""
        from PyQt6.QtWidgets import QFileDialog

        base_dir = QFileDialog.getExistingDirectory(self, "选择输出根目录（每个版本将写入独立子目录）")
        if not base_dir:
            return

        proj = self._ctx.active_project
        variants = proj.variants
        current_variant = self._ctx.active_variant
        current_vs = self._ctx.variant_store
        collection = self._ctx.collection

        results = []
        for v in variants:
            vname = v["name"]
            out_dir = Path(base_dir) / vname
            out_dir.mkdir(parents=True, exist_ok=True)

            # 切换到目标版本的译文
            if vname != current_variant:
                vs_path = proj.variant_dir(vname) / "current.json"
                from transbridge.persistence import VariantStore

                vs = VariantStore.load(vs_path)
                vs.apply_to(list(collection))
            else:
                vs = current_vs

            # 写回到子目录
            try:
                self._output_dir_override = out_dir
                if target == "esp":
                    self._write_esp(collection)
                elif target == "eet":
                    self._write_eet(collection, dlg.eet_path)
                elif target == "xt":
                    self._write_xt(collection, dlg.xt_path)
                else:
                    self._write_dsd(collection)
                results.append(f"✅ {vname}")
            except Exception as e:
                results.append(f"❌ {vname}: {e}")
            finally:
                self._output_dir_override = None

        # 恢复原始版本
        if current_vs:
            current_vs.apply_to(list(collection))

        QMessageBox.information(self, "批量写回完成", "\n".join(results))

    # ── Write ESP ─────────────────────────────────────────────

    def _write_esp(self, collection):
        esp_path = self._ctx.esp_path
        if not esp_path:
            QMessageBox.warning(self, "未找到插件路径", "请先在步骤1中解析插件文件，再执行写回操作。")
            return

        plugin = getattr(self._ctx, "plugin", None)
        strings_lookup = getattr(self._ctx, "strings_lookup", None)
        strings_lang = getattr(self._ctx, "strings_lang", "chinese")
        is_localized = strings_lookup is not None

        if plugin is None:
            QMessageBox.warning(
                self,
                "写回服务未接入",
                "当前翻译内容来自权威建项 hydration，必须通过操作计划写回；不会重新解析源插件或直接写正式文件。",
            )
            return

        src = Path(esp_path)

        # 本地化插件：让用户选择 strings 输出目录
        if is_localized:
            if self._output_dir_override:
                output_dir = str(self._output_dir_override)
            else:
                output_dir = QFileDialog.getExistingDirectory(
                    self,
                    "选择 Strings 文件输出目录",
                    str(src.parent),
                )
            if not output_dir:
                return
            # 使用原插件名作为 strings 文件前缀
            esp_output_path = Path(output_dir) / src.name
        else:
            # 非本地化插件：让用户选择 ESP 保存路径
            if self._output_dir_override:
                esp_output_path = self._output_dir_override / src.name
            else:
                default_name = src.stem + "_translated" + src.suffix
                esp_output_path_str, _ = QFileDialog.getSaveFileName(
                    self,
                    "保存汉化插件",
                    str(src.parent / default_name),
                    "ESP/ESM 文件 (*.esp *.esm);;所有文件 (*)",
                )
                if not esp_output_path_str:
                    return
                esp_output_path = Path(esp_output_path_str)

        def _write():
            writer = PluginWriter(plugin, strings_lookup=strings_lookup, language=strings_lang)
            count = writer.apply_collection(collection)
            write_result = writer.write(esp_output_path)
            return count, is_localized, esp_output_path, write_result

        def _on_done(result):
            count, localized, out_path, write_result = result
            esp_saved = write_result.get("esp_saved", True)
            strings_written = write_result.get("strings_written", [])

            if localized and strings_written:
                strings_dir = strings_written[0].parent
                if esp_saved:
                    # 混合模式：ESP + strings 文件
                    QMessageBox.information(
                        self,
                        "写回完成",
                        f"已写入 {count} 条译文。\n\nESP 文件：{out_path}\n\nStrings 文件：{strings_dir}",
                    )
                else:
                    # 纯本地化模式：只有 strings 文件
                    strings_list = "\n".join(f"  • {p.name}" for p in strings_written)
                    QMessageBox.information(
                        self,
                        "写回完成",
                        f"已写入 {count} 条译文到 Strings 文件。\n\n"
                        f"输出目录：{strings_dir}\n\n"
                        f"生成的文件：\n{strings_list}",
                    )
            else:
                QMessageBox.information(
                    self,
                    "写回完成",
                    f"已写入 {count} 条译文，保存至：\n{out_path}",
                )

        self._run_worker(
            _write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "写回失败", e),
            progress_total=0,
            progress_msg="正在写回 ESP 插件…",
        )

    # ── Write EET XML ─────────────────────────────────────────

    def _write_eet(self, collection, src_path: str):
        if self._output_dir_override:
            save_path = str(self._output_dir_override / Path(src_path).name)
        else:
            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存 EET XML",
                src_path,
                "XML 文件 (*.xml);;所有文件 (*)",
            )
        if not save_path:
            return

        def _write():
            parser = EET_XmlParser.from_file(src_path)
            writer = EETWriter(parser)
            count = writer.apply_collection(collection)
            writer.write(save_path)
            return count, Path(save_path)

        def _on_done(result):
            count, out_path = result
            QMessageBox.information(
                self,
                "写回完成",
                f"已更新 {count} 条译文，保存至：\n{out_path}",
            )

        self._run_worker(
            _write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "EET 写回失败", e),
            progress_total=0,
            progress_msg="正在写回 EET XML…",
        )

    # ── Write XT XML ──────────────────────────────────────────

    def _write_xt(self, collection, src_path: str):
        if self._output_dir_override:
            save_path = str(self._output_dir_override / Path(src_path).name)
        else:
            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存 XT XML",
                src_path,
                "XML 文件 (*.xml);;所有文件 (*)",
            )
        if not save_path:
            return

        def _write():
            parser = XT_XmlParser.from_file(src_path)
            writer = XTWriter(parser)
            count = writer.apply_collection(collection)
            writer.write(save_path)
            return count, Path(save_path)

        def _on_done(result):
            count, out_path = result
            QMessageBox.information(
                self,
                "写回完成",
                f"已更新 {count} 条译文，保存至：\n{out_path}",
            )

        self._run_worker(
            _write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "XT 写回失败", e),
            progress_total=0,
            progress_msg="正在写回 XT XML…",
        )

    # ── Write DSD JSON ────────────────────────────────────────

    def _write_dsd(self, collection):
        """导出为 DSD 格式 JSON 文件。"""
        esp_path = self._ctx.esp_path
        if esp_path:
            default_name = Path(esp_path).stem + "_dsd.json"
            default_dir = str(Path(esp_path).parent)
        else:
            default_name = "export_dsd.json"
            default_dir = ""

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 DSD JSON",
            str(Path(default_dir) / default_name) if default_dir else default_name,
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not save_path:
            return

        def _write():
            # 统计有译文的条目数
            count = sum(1 for e in collection if e.translation)
            collection.to_dsd_json_file(save_path)
            return count, Path(save_path)

        def _on_done(result):
            count, out_path = result
            if count == 0:
                QMessageBox.warning(
                    self,
                    "导出完成",
                    f"没有已翻译的条目，导出文件为空。\n\n保存至：{out_path}",
                )
            else:
                QMessageBox.information(
                    self,
                    "导出完成",
                    f"已导出 {count} 条译文到 DSD JSON。\n\n保存至：{out_path}",
                )

        self._run_worker(
            _write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "DSD 导出失败", e),
            progress_total=0,
            progress_msg="正在导出 DSD JSON…",
        )
