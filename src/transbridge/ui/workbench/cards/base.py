from PyQt6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class OpCard(QGroupBox):
    """单个操作卡片：标题 + 说明 + 操作按钮 + 可选批量按钮。"""

    def __init__(self, title: str, desc: str, btn_text: str, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(title, parent)
        self._theme_view = theme_view
        self._operation_plan_facade = None
        ComponentStyle.apply_static(self, ComponentKind.CARD)
        layout = QVBoxLayout(self)
        lbl = QLabel(desc)
        lbl.setWordWrap(True)
        lbl.setAccessibleName(f"{title}说明")
        layout.addWidget(lbl)
        layout.addStretch()

        # 按钮行：主按钮 + 批量按钮
        btn_row = QHBoxLayout()
        self.btn = QPushButton(btn_text)
        self.btn.setFixedHeight(30)
        btn_row.addWidget(self.btn)

        self._batch_btn = QPushButton("批量")
        self._batch_btn.setFixedHeight(30)
        self._batch_btn.setFixedWidth(50)
        self._batch_btn.setVisible(False)  # 默认隐藏
        btn_row.addWidget(self._batch_btn)

        layout.addLayout(btn_row)

    @property
    def batch_btn(self) -> QPushButton:
        """返回批量按钮，供外部设置点击事件。"""
        return self._batch_btn

    def set_batch_visible(self, visible: bool):
        """设置批量按钮可见性。"""
        self._batch_btn.setVisible(visible)

    def bind_operation_plan_facade(self, facade) -> None:
        """Composition hook; cards retain their old public intent facade."""
        self._operation_plan_facade = facade

    def _dispatch_planned(self, intent: str, context, *, batch: bool = False) -> bool:
        facade = self._operation_plan_facade
        if facade is None:
            return False
        handler = getattr(facade, f"begin_{intent}", None)
        if not callable(handler):
            return False
        supports = getattr(facade, "supports", None)
        if callable(supports) and not supports(intent, context, batch=batch):
            return False
        handler(context, batch=batch)
        return True
