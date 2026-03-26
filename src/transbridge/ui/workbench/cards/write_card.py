from pathlib import Path
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QRadioButton, QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox,
    QButtonGroup, QScrollArea, QWidget,
)

from src.transbridge.writer.plugin_writer import PluginWriter
from src.transbridge.writer.eet_xml_writer import EETWriter
from src.transbridge.writer.xt_xml_writer import XTWriter
from src.transbridge.parser.eet_parser import EET_XmlParser
from src.transbridge.parser.xt_parser import XT_XmlParser
from .base import OpCard


@dataclass
class BatchWriteResult:
    """批量写回结果汇总。"""
    success_count: int = 0
    failed_count: int = 0
    total_entries: int = 0
    details: list[str] = field(default_factory=list)


class _BatchConfirmDialog(QDialog):
    """批量操作确认对话框，带滚动区域。"""

    def __init__(self, title: str, header: str, items: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        # 标题说明
        header_lbl = QLabel(header)
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            lbl.setStyleSheet("color: #333;")
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        btn_box.button(QDialogButtonBox.StandardButton.Yes).setText("确认")
        btn_box.button(QDialogButtonBox.StandardButton.No).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class _BatchResultDialog(QDialog):
    """批量操作结果对话框，带滚动区域。"""

    def __init__(self, title: str, header: str, items: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        # 标题说明
        header_lbl = QLabel(header)
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            lbl.setStyleSheet("color: #333;")
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)


class _WriteTargetDialog(QDialog):
    """写回目标选择对话框。"""

    def __init__(self, eet_path: str | None, xt_path: str | None,
                 has_esp: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择写回目标")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        group = QButtonGroup(self)

        # ── ESP ───────────────────────────────────────────────
        self._rb_esp = QRadioButton("写回 ESP 插件")
        self._rb_esp.setChecked(has_esp)
        self._rb_esp.setEnabled(has_esp)
        if has_esp:
            esp_desc = QLabel("将译文写入插件副本，输出汉化版 ESP 文件。")
        else:
            esp_desc = QLabel("当前集合由 EET XML 构建，无法写回 ESP 插件。")
        esp_desc.setStyleSheet("color: #555; margin-left: 20px;")
        group.addButton(self._rb_esp)
        layout.addWidget(self._rb_esp)
        layout.addWidget(esp_desc)
        layout.addSpacing(6)

        # ── EET XML ───────────────────────────────────────────
        self._rb_eet = QRadioButton("写回 EET XML")
        eet_desc = QLabel("将译文更新到 EET XML 文件中。")
        eet_desc.setStyleSheet("color: #555; margin-left: 20px;")
        eet_path_row = QHBoxLayout()
        eet_path_lbl = QLabel("路径：")
        eet_path_lbl.setStyleSheet("margin-left: 20px;")
        eet_path_lbl.setFixedWidth(40)
        self._eet_input = QLineEdit(eet_path or "")
        self._eet_input.setPlaceholderText("选择 EET XML 文件…")
        self._eet_input.setEnabled(False)
        self._eet_browse = QPushButton("浏览")
        self._eet_browse.setFixedWidth(50)
        self._eet_browse.setEnabled(False)
        self._eet_browse.clicked.connect(self._browse_eet)
        eet_path_row.addWidget(eet_path_lbl)
        eet_path_row.addWidget(self._eet_input)
        eet_path_row.addWidget(self._eet_browse)
        group.addButton(self._rb_eet)
        layout.addWidget(self._rb_eet)
        layout.addWidget(eet_desc)
        layout.addLayout(eet_path_row)
        layout.addSpacing(6)

        # ── XT XML ────────────────────────────────────────────
        self._rb_xt = QRadioButton("导出译文")
        xt_desc = QLabel("将译文更新到 XT XML 文件中。")
        xt_desc.setStyleSheet("color: #555; margin-left: 20px;")
        xt_path_row = QHBoxLayout()
        xt_path_lbl = QLabel("路径：")
        xt_path_lbl.setStyleSheet("margin-left: 20px;")
        xt_path_lbl.setFixedWidth(40)
        self._xt_input = QLineEdit(xt_path or "")
        self._xt_input.setPlaceholderText("选择 XT XML 文件…")
        self._xt_input.setEnabled(False)
        self._xt_browse = QPushButton("浏览")
        self._xt_browse.setFixedWidth(50)
        self._xt_browse.setEnabled(False)
        self._xt_browse.clicked.connect(self._browse_xt)
        xt_path_row.addWidget(xt_path_lbl)
        xt_path_row.addWidget(self._xt_input)
        xt_path_row.addWidget(self._xt_browse)
        group.addButton(self._rb_xt)
        layout.addWidget(self._rb_xt)
        layout.addWidget(xt_desc)
        layout.addLayout(xt_path_row)
        layout.addSpacing(8)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认写回")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._rb_eet.toggled.connect(self._on_mode_changed)
        self._rb_xt.toggled.connect(self._on_mode_changed)
        self._eet_input.textChanged.connect(self._update_ok)
        self._xt_input.textChanged.connect(self._update_ok)

        # 无 ESP 时默认选中 EET
        if not has_esp:
            self._rb_eet.setChecked(True)
            self._on_mode_changed()

    def _on_mode_changed(self):
        eet = self._rb_eet.isChecked()
        xt = self._rb_xt.isChecked()
        self._eet_input.setEnabled(eet)
        self._eet_browse.setEnabled(eet)
        self._xt_input.setEnabled(xt)
        self._xt_browse.setEnabled(xt)
        self._update_ok()

    def _update_ok(self):
        if self._rb_eet.isChecked():
            self._ok_btn.setEnabled(bool(self._eet_input.text().strip()))
        elif self._rb_xt.isChecked():
            self._ok_btn.setEnabled(bool(self._xt_input.text().strip()))
        else:
            self._ok_btn.setEnabled(True)

    def _browse_eet(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 EET XML 文件", self._eet_input.text(), "XML 文件 (*.xml);;所有文件 (*)"
        )
        if path:
            self._eet_input.setText(path)

    def _browse_xt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 XT XML 文件", self._xt_input.text(), "XML 文件 (*.xml);;所有文件 (*)"
        )
        if path:
            self._xt_input.setText(path)

    @property
    def target(self) -> str:
        if self._rb_eet.isChecked():
            return "eet"
        if self._rb_xt.isChecked():
            return "xt"
        return "esp"

    @property
    def eet_path(self) -> str:
        return self._eet_input.text().strip()

    @property
    def xt_path(self) -> str:
        return self._xt_input.text().strip()


class WriteCard(OpCard):

    def __init__(self, ctx, run_worker, parent=None):
        super().__init__(
            "写回插件/XML",
            "将集合中的译文写回 ESP 插件或 EET/XT XML 文件。",
            "写回",
            parent,
        )
        self._ctx = ctx
        self._run_worker = run_worker
        self.btn.clicked.connect(self._do_write)
        # eet_btn / xt_btn 作为 step3 按钮状态管理的占位符，与 self.btn 相同
        self.eet_btn = self.btn
        self.xt_btn = self.btn

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
        output_dir = QFileDialog.getExistingDirectory(
            self, "选择批量写回输出目录"
        )
        if not output_dir:
            return
        output_dir = Path(output_dir)

        def _batch_write():
            results = BatchWriteResult()
            total = len(selected_slots)

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
                    esp_saved = write_result.get("esp_saved", True)
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
            dlg = _BatchResultDialog(
                "批量写回完成", header, result.details, parent=self
            )
            dlg.exec()

        self._run_worker(
            _batch_write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "批量写回失败", e),
            progress_total=0,
            progress_msg="正在批量写回…",
        )

    def _do_write(self):
        collection = self._ctx.collection
        if not collection:
            return

        dlg = _WriteTargetDialog(
            self._ctx.eet_path,
            self._ctx.xt_path,
            has_esp=self._ctx.plugin is not None,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        target = dlg.target
        if target == "esp":
            self._write_esp(collection)
        elif target == "eet":
            self._write_eet(collection, dlg.eet_path)
        else:
            self._write_xt(collection, dlg.xt_path)

    # ── Write ESP ─────────────────────────────────────────────

    def _write_esp(self, collection):
        esp_path = self._ctx.esp_path
        if not esp_path:
            QMessageBox.warning(self, "未找到插件路径",
                                "请先在步骤1中解析插件文件，再执行写回操作。")
            return

        plugin = getattr(self._ctx, "plugin", None)
        strings_lookup = getattr(self._ctx, "strings_lookup", None)
        strings_lang = getattr(self._ctx, "strings_lang", "chinese")
        is_localized = strings_lookup is not None

        if plugin is None:
            QMessageBox.warning(self, "插件未加载",
                                "未找到已解析的插件实例，请重新在步骤1中解析插件文件。")
            return

        src = Path(esp_path)

        # 本地化插件：让用户选择 strings 输出目录
        if is_localized:
            output_dir = QFileDialog.getExistingDirectory(
                self, "选择 Strings 文件输出目录",
                str(src.parent),
            )
            if not output_dir:
                return
            # 使用原插件名作为 strings 文件前缀
            esp_output_path = Path(output_dir) / src.name
        else:
            # 非本地化插件：让用户选择 ESP 保存路径
            default_name = src.stem + "_translated" + src.suffix
            esp_output_path_str, _ = QFileDialog.getSaveFileName(
                self, "保存汉化插件", str(src.parent / default_name),
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
                        self, "写回完成",
                        f"已写入 {count} 条译文。\n\n"
                        f"ESP 文件：{out_path}\n\n"
                        f"Strings 文件：{strings_dir}",
                    )
                else:
                    # 纯本地化模式：只有 strings 文件
                    strings_list = "\n".join(f"  • {p.name}" for p in strings_written)
                    QMessageBox.information(
                        self, "写回完成",
                        f"已写入 {count} 条译文到 Strings 文件。\n\n"
                        f"输出目录：{strings_dir}\n\n"
                        f"生成的文件：\n{strings_list}",
                    )
            else:
                QMessageBox.information(
                    self, "写回完成",
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
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存 EET XML", src_path,
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
                self, "写回完成",
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
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存 XT XML", src_path,
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
                self, "写回完成",
                f"已更新 {count} 条译文，保存至：\n{out_path}",
            )

        self._run_worker(
            _write,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "XT 写回失败", e),
            progress_total=0,
            progress_msg="正在写回 XT XML…",
        )
