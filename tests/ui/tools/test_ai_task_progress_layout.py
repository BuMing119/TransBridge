from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QAbstractItemView, QTreeWidgetItem

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app  # noqa: F401
from tests.ui.tools import test_unified_task_progress as fixtures
from transbridge.ai_translator.activity import ActivityKind, ActivityMessage
from transbridge.ui.tools.ai_translator.task_activity import ActivityBatch

make_window = fixtures.make_window
qapp = fixtures.qapp


def _project_progress(window):
    window.run.progress_view = SimpleNamespace(
        summary="项目名称首批 · 翻译名称",
        source_rows={
            "first": SimpleNamespace(
                phase="翻译名称",
                current=45,
                total=2158,
                count_text="45 / 2,158 个名称 · 2%",
                detail="正在翻译第 3 批名称",
            ),
            "second": SimpleNamespace(
                phase="等待名称共享",
                current=0,
                total=0,
                count_text="",
                detail="统一名称准备完成后开始正文",
            ),
        },
    )
    window.run.progress = (45, 2158, "项目名称 %v / %m")
    window._refresh()


def test_project_summary_four_columns_and_compact_activity(layout_app, make_window):
    window, _, _ = make_window()
    _project_progress(window)
    window.run.logged.emit("开始汇总所有插件的名称")
    window.show()
    settle_layout(layout_app)

    assert [window.sources.headerItem().text(index) for index in range(4)] == ["处理内容", "阶段", "进度", "当前处理"]
    assert window.project_summary.text() == "项目名称首批 · 翻译名称"
    assert window.project_summary.geometry().bottom() < window.bar.geometry().top()
    assert window.bar.value() == 45 and window.bar.maximum() == 2158
    assert [window.rows["first"].text(index) for index in range(1, 4)] == [
        "翻译名称",
        "45 / 2,158 个名称 · 2%",
        "正在翻译第 3 批名称",
    ]
    assert window.rows["second"].text(2) == "—"
    assert not window.logs.isVisible() and not window.log_toggle.isChecked()
    assert window.activity_panel.recent.isVisible()
    assert "开始汇总所有插件的名称" in window.logs.toPlainText()
    window.log_toggle.click()
    settle_layout(layout_app)
    assert window.logs.isVisible() and window.log_toggle.text() == "收起动态记录"
    assert window.rect().contains(window.stop_button.geometry())
    window.log_toggle.click()
    settle_layout(layout_app)
    assert not window.logs.isVisible() and window.log_toggle.text() == "展开动态记录"
    assert window.activity_panel.recent.isVisible()


def test_many_sources_scroll_without_growing_beyond_six_visible_rows(layout_app, make_window):
    window, _, _ = make_window()
    window.show()
    settle_layout(layout_app)
    two_rows_height = window.sources.height()
    for index in range(4):
        window.sources.addTopLevelItem(QTreeWidgetItem([f"插件 {index}", "等待", "—", "等待名称共享"]))
    window.sources.updateGeometry()
    settle_layout(layout_app)
    six_rows_height = window.sources.height()
    assert six_rows_height > two_rows_height
    seventh = QTreeWidgetItem(["第七个插件", "等待", "—", "等待名称共享"])
    window.sources.addTopLevelItem(seventh)
    window.sources.updateGeometry()
    settle_layout(layout_app)
    assert window.sources.height() == six_rows_height
    assert window.sources.verticalScrollBar().maximum() > 0
    assert not window.sources.viewport().rect().intersects(window.sources.visualItemRect(seventh))
    window.sources.scrollToItem(seventh, QAbstractItemView.ScrollHint.PositionAtBottom)
    settle_layout(layout_app)
    assert window.sources.verticalScrollBar().value() > 0
    assert window.sources.viewport().rect().contains(window.sources.visualItemRect(seventh))
    for index in range(13):
        window.sources.addTopLevelItem(QTreeWidgetItem([f"后续插件 {index}", "等待", "—", "等待名称共享"]))
    window.sources.updateGeometry()
    settle_layout(layout_app)

    assert window.sources.height() == six_rows_height
    assert window.sources.verticalScrollBar().maximum() > 0
    assert not window.logs.isVisible() and window.activity_panel.recent.isVisible()


def test_legacy_rows_and_terminal_results_replace_stale_source_progress(layout_app, make_window):
    window, _, _ = make_window()
    _project_progress(window)
    window.run.rows["first"] = ("处理结束", "成功 2 条，失败 1 条")
    window.run.rows["second"] = ("已取消", "未应用任何结果")
    window._refresh()
    assert [window.rows["first"].text(index) for index in range(1, 4)] == [
        "处理结束",
        "—",
        "成功 2 条，失败 1 条",
    ]
    assert window.rows["second"].text(1) == "已取消"
    window.run.progress_view = None
    window.run.rows["first"] = ("翻译", "2/5 · 正在翻译")
    window._refresh()
    assert not window.project_summary.isVisibleTo(window)
    assert [window.rows["first"].text(index) for index in range(1, 4)] == ["翻译", "—", "2/5 · 正在翻译"]


def test_narrow_layout_elides_long_details_and_adapts_table_height_to_font(layout_app, make_window, monkeypatch):
    long_label = "很长的插件名称-" * 12
    monkeypatch.setattr(fixtures.task_progress, "source_label", lambda *args: long_label)
    window, _, _ = make_window()
    _project_progress(window)
    long_detail = "正在翻译一段带有完整上下文的名称 " * 30
    window.run.progress_view.source_rows["first"].detail = long_detail
    window._refresh()
    window.resize(720, 680)
    window.show()
    settle_layout(layout_app)
    previous_height = window.sources.height()
    window.sources.setStyleSheet("font-size: 24px;")
    window.sources.updateGeometry()
    settle_layout(layout_app)

    assert window.width() == 720
    assert window.sources.height() > previous_height
    assert window.rows["first"].toolTip(0) == long_label
    assert window.rows["first"].toolTip(3) == long_detail
    assert window.sources.visualItemRect(window.rows["second"]).bottom() < window.sources.viewport().height()
    assert window.activity_panel.recent.isVisible()
    assert window.rect().contains(window.activity_panel.geometry())
    assert window.rect().contains(window.stop_button.geometry())


def test_expanded_activity_and_warnings_fit_narrow_window(layout_app, make_window, tmp_path):
    window, _, _ = make_window()
    _project_progress(window)
    window.resize(720, 680)
    window.run.logged.emit(
        ActivityBatch((
            ("Skyrim.esm", ActivityMessage("已复用 1,071 个名称，无需再次请求模型。")),
            (
                "Update.esm",
                ActivityMessage("6/12 条未获译文，正在对半拆分重试，暂时无需操作。", ActivityKind.RETRY, batch=3),
            ),
            (
                "Skyrim.esm",
                ActivityMessage(
                    "12 条模型请求失败；本次未获得有效结果，处理结束后可重试失败条目。", ActivityKind.ERROR, batch=7
                ),
            ),
        ))
    )
    window.show()
    settle_layout(layout_app)
    assert window.width() == 720 and window.height() == 680
    assert window.activity_panel.notice.isVisible()
    assert window.grab().save(str(tmp_path / "activity-compact.png"))
    window.log_toggle.click()
    settle_layout(layout_app)
    assert window.width() == 720 and window.height() == 680
    assert window.logs.isVisible()
    assert window.rect().contains(window.activity_panel.geometry())
    assert window.rect().contains(window.stop_button.geometry())
    assert window.grab().save(str(tmp_path / "activity-expanded.png"))
