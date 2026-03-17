from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from src.transbridge.writer.plugin_writer import PluginWriter
from .base import OpCard


class WriteCard(OpCard):

    def __init__(self, ctx, run_worker, parent=None):
        super().__init__(
            "写回 ESP 插件",
            "将集合中的译文写入插件副本，输出汉化版 ESP 文件。",
            "选择输出路径",
            parent,
        )
        self._ctx = ctx
        self._run_worker = run_worker
        self.btn.clicked.connect(self._do_write_esp)

    def _do_write_esp(self):
        collection = self._ctx.collection
        if not collection:
            return

        esp_path = self._ctx.esp_path
        if not esp_path:
            QMessageBox.warning(self, "未找到插件路径",
                                "请先在步骤1中解析插件文件，再执行写回操作。")
            return

        plugin = getattr(self._ctx, "plugin", None)
        strings_lookup = getattr(self._ctx, "strings_lookup", None)
        is_localized = strings_lookup is not None

        if plugin is None:
            QMessageBox.warning(self, "插件未加载",
                                "未找到已解析的插件实例，请重新在步骤1中解析插件文件。")
            return

        src = Path(esp_path)
        default_name = src.stem + "_translated" + src.suffix
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存汉化插件", str(src.parent / default_name),
            "ESP/ESM 文件 (*.esp *.esm);;所有文件 (*)",
        )
        if not save_path:
            return

        def _write():
            writer = PluginWriter(plugin, strings_lookup=strings_lookup)
            count = writer.apply_collection(collection)
            writer.write(save_path)
            return count, is_localized, Path(save_path)

        def _on_done(result):
            count, localized, out_path = result
            if localized:
                strings_dir = out_path.parent / "Strings"
                QMessageBox.information(
                    self, "写回完成",
                    f"已写入 {count} 条译文，保存至：\n{out_path}\n\n"
                    f"本地化插件，Strings 文件已输出至：\n{strings_dir}",
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
