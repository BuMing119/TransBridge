import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.terminology import Page
from transbridge.application.terminology.models import TermScope
from transbridge.application.terminology_profiles import (
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
)
from transbridge.ui.tools.terminology.pagination_bar import PaginationBar
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.table_models import make_table_model
from transbridge.ui.tools.terminology.window import TerminologyWindow
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBarState, TerminologyProfileChoice

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_missing_query_service_is_reported_by_pagination_instead_of_empty_success():
    presenter = TerminologyPresenter(
        TerminologyUiServices(),
        RequestContext("owner", project_id="project", variant_id="variant"),
    )
    with pytest.raises(RuntimeError) as missing:
        presenter.page_loader("list_effective_terms")
    model = make_table_model(presenter, "effective", None)
    bar = PaginationBar(model)
    try:
        model.set_query(("project", "variant"))
        _wait(lambda: not model.is_loading)
        assert model.last_error == str(missing.value)
        assert model.pagination.total is None
        assert str(missing.value) in bar.notice.text()
        assert not bar.notice.isHidden()
        assert bar.refresh_button.isEnabled()
    finally:
        model.close()
        bar.close()


def _profile_state(name):
    content = TerminologyProfileContent((ProfileTermMapping("Dragon", name),))
    revision = PublishedTerminologyProfile(
        name,
        "project",
        1,
        name,
        content.content_digest,
        content,
        "2026-10-14T00:00:00Z",
    )
    return TerminologyProfileBarState(
        choices=(TerminologyProfileChoice("A", "A"), TerminologyProfileChoice("B", "B")),
        selected_profile_id=name,
        enabled=True,
        can_manage=True,
        selected_revision=revision,
    )


class _Profiles(QObject):
    state_changed = pyqtSignal(object)
    identity = ("project", "variant")

    def __init__(self):
        super().__init__()
        self.state = _profile_state("A")


@pytest.mark.parametrize("new_query_fails", [False, True], ids=["new-page-ready", "new-page-failed"])
def test_profile_switch_invalidates_old_page_even_when_asset_refresh_fails(new_query_fails):
    entered, release, asset_failed = threading.Event(), threading.Event(), threading.Event()
    profiles = _Profiles()

    class Browsing:
        def list_effective_terms(self, _query, _request, *, cancellation=None):
            selected = profiles.state.selected_profile_id
            if selected == "A":
                entered.set()
                assert release.wait(5)
            elif new_query_fails:
                raise RuntimeError("new B page unavailable")
            row = SimpleNamespace(original="Dragon", translation=selected, scope=TermScope.project())
            return Page((row,), f"profile-{selected}", total=1)

        def list_browsable_draft_terms(self, _query, _request, *, cancellation=None):
            return Page((), "draft", total=0)

        def describe(self, _query, *, cancellation=None):
            asset_failed.set()
            raise RuntimeError("asset metadata unavailable after switching")

    window = TerminologyWindow(
        TerminologyPresenter(
            TerminologyUiServices(browsing=Browsing()),
            RequestContext("owner", project_id="project", variant_id="variant"),
        ),
        ui_context=SimpleNamespace(active_version_identity=("project", "variant")),
        profile_controller=profiles,
    )
    try:
        window._browsing.refresh()
        assert entered.wait(2)
        previous_generation = window.effective_model.generation
        profiles.state = _profile_state("B")
        profiles.state_changed.emit(profiles.state)
        assert window.effective_model.generation > previous_generation
        _wait(lambda: asset_failed.is_set() and not window.effective_model.is_loading)
        browser = window.terms_view.source_browser
        assert browser.model.index(0, 1).data() == "B"
        assert browser.model.index(0, 3).data() == "尚未检查匹配状态"
        release.set()
        assert QThreadPool.globalInstance().waitForDone(3000)
        _APP.processEvents()
        if new_query_fails:
            assert window.effective_model.rowCount() == 0
            assert window.effective_model.last_error == "new B page unavailable"
        else:
            assert window.effective_model.index(0, 1).data() == "B"
            assert window.effective_model.last_error is None
    finally:
        release.set()
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)
