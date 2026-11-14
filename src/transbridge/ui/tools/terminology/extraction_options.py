# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""User choices for a project terminology extraction run."""

from PyQt6.QtCore import QSignalBlocker, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ExtractionOptionsView(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._user_changed = False
        self._restored = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        choices = QHBoxLayout()
        choices.addWidget(QLabel("提取方式", self))
        self.mode = QComboBox(self)
        self.mode.setAccessibleName("术语提取方式")
        self.mode.addItem("规则整理＋LLM 抽取（推荐）", True)
        self.mode.addItem("仅规则整理", False)
        choices.addWidget(self.mode, 1)
        self.force = QCheckBox("强制重新抽取", self)
        self.force.setToolTip("重新请求模型；不复用之前成功的抽取批次。")
        choices.addWidget(self.force)
        layout.addLayout(choices)
        self.model = QLabel("使用模型：检查后显示", self)
        self.model.setWordWrap(True)
        layout.addWidget(self.model)
        self.description = QLabel(
            "名称字段按规则收集；正文由 LLM 抽取。抽取后由 LLM 判断真假冲突，复用抽取缓存也会执行。"
            "无差异项默认采用，正常表达差异自动归并；剩余冲突由你处理，应用后生效。",
            self,
        )
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.force.toggled.connect(self._user_change)

    def configuration(self, *, user_action=False):
        if user_action:
            self._user_changed = True
        enabled = bool(self.mode.currentData())
        return {"llm_enabled": enabled, "force_llm": enabled and self.force.isChecked()}

    def restore_from_build(self, build):
        """Restore the first persisted mode without overriding an explicit choice."""
        if self._restored or self._user_changed:
            return False
        self._restored = True
        if build is None:
            return False
        diagnostics = getattr(build, "diagnostics", ())
        status = str(getattr(build, "llm_status", ""))
        if "LLM_DISABLED" in diagnostics:
            enabled = False
        elif "LLM_NO_TEXT" in diagnostics:
            enabled = True
        elif status == "skipped":
            enabled = bool(getattr(build, "extracted_count", 0)) or any(
                str(item.extraction_method) == "llm_text" for item in getattr(build, "candidates", ())
            )
        elif status in {"performed", "partial", "unavailable"}:
            enabled = True
        else:
            return False
        with QSignalBlocker(self.mode), QSignalBlocker(self.force):
            self.mode.setCurrentIndex(self.mode.findData(enabled))
            self.force.setChecked(False)
        self._render_mode()
        return True

    def _mode_changed(self):
        self._render_mode()
        self._user_change()

    def _render_mode(self):
        enabled = bool(self.mode.currentData())
        self.force.setEnabled(enabled)
        self.model.setText("使用模型：检查后显示" if enabled else "仅规则整理，不调用 LLM")
        self.description.setText(
            "名称字段按规则收集；正文由 LLM 抽取。抽取后由 LLM 判断真假冲突，复用抽取缓存也会执行。"
            "无差异项默认采用，正常表达差异自动归并；剩余冲突由你处理，应用后生效。"
            if enabled
            else "仅收集名称字段中的原文和已有译文，不从对话或正文中识别术语。"
        )

    def _user_change(self):
        self._user_changed = True
        self.changed.emit()
