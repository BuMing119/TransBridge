from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.ui.tools.terminology.source_browser import SourceBrowser
from transbridge.ui.tools.terminology.source_context_loader import SourceContextLoader
from transbridge.ui.tools.terminology.term_browsing import TermBrowsingController

_APP = QApplication.instance() or QApplication([])


class Pool:
    def __init__(self):
        self.calls = []

    def start(self, call):
        self.calls.append(call)


def test_source_mappings_load_on_show_and_cancel_stale_hidden_result():
    pool, calls = Pool(), []
    row = SimpleNamespace(
        original="Dragon",
        translation="龙",
        scope_kind="project",
        plugin_id=None,
        state="effective",
        base_translation="龙",
    )
    context = SimpleNamespace(project_id="p", variant_id="v")
    result = SimpleNamespace(profile_id="profile", mapping_rows=(row,), diagnostics=())

    def describe(query, *, cancellation):
        calls.append(cancellation)
        return result

    browser = SourceBrowser()
    loader = SourceContextLoader(browser, SimpleNamespace(describe=describe), context, thread_pool=pool)
    try:
        loader.invalidate()
        _APP.processEvents()
        assert not pool.calls
        browser.show()
        _APP.processEvents()
        assert len(pool.calls) == 1
        pool.calls[0].run()
        browser.hide()
        _APP.processEvents()
        assert browser.model.rowCount() == 0
        browser.show()
        _APP.processEvents()
        assert len(pool.calls) == 2
        pool.calls[1].run()
        _APP.processEvents()
        assert browser.model.rowCount() == 1
        assert browser.table.isEnabled()
        loader.invalidate()
        _APP.processEvents()
        assert len(pool.calls) == 3
        loader.close()
        pool.calls[2].run()
        _APP.processEvents()
        assert len(calls) == 2
    finally:
        loader.close()
        browser.close()


def test_hidden_pages_do_not_read_until_opened():
    calls = []

    def model(name):
        return SimpleNamespace(
            clear=lambda: None,
            refresh=lambda: calls.append(name),
            set_query=lambda *_args, **_kwargs: calls.append(name),
        )

    effective, draft = QWidget(), QWidget()
    controller = TermBrowsingController(
        SimpleNamespace(project_id="p", variant_id="v"), model("effective"), model("draft")
    )
    controller.bind_views(effective=effective, draft=draft)
    try:
        controller.refresh()
        assert calls == []
        effective.show()
        _APP.processEvents()
        assert calls == ["effective"]
        controller.refresh()
        assert calls == ["effective", "effective"]
        draft.show()
        _APP.processEvents()
        assert calls[-1] == "draft"
    finally:
        effective.close()
        draft.close()
