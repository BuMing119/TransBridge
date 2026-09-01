from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.context import AppContext
from transbridge.ui.paratranz.export_tab import ExportTab
from transbridge.ui.paratranz.files_tab import FilesTab
from transbridge.ui.paratranz.members_tab import MembersTab
from transbridge.ui.paratranz.overview_tab import OverviewTab
from transbridge.ui.paratranz.strings_tab import StringsTab


class ManualWorker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, _callback, **_kwargs):
        super().__init__()

    def start(self):
        pass


@pytest.fixture
def pages(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token="fake"))
    for module in ("overview_tab", "strings_tab", "files_tab", "members_tab", "export_tab"):
        monkeypatch.setattr(f"transbridge.ui.paratranz.{module}.ApiWorker", ManualWorker)
    context = AppContext()
    context.current_user = {"id": 2}
    views = SimpleNamespace(
        ctx=context,
        overview=OverviewTab(context),
        strings=StringsTab(context),
        files=FilesTab(context),
        members=MembersTab(context),
        export=ExportTab(context),
    )
    context.current_project = {"id": 7, "uid": 99, "name": "Project", "download": 2}
    yield views
    for key, widget in vars(views).items():
        if key != "ctx":
            widget.close()
            widget.deleteLater()
    context.deleteLater()
    app.processEvents()


def test_async_members_enable_admin_actions_without_reloading_projects(pages):
    assert not pages.ctx.is_admin()
    assert pages.overview._edit_btn.isHidden()
    assert not pages.strings._create_btn.isEnabled()
    counts = [len(page._workers) for page in (pages.files, pages.strings, pages.overview, pages.export)]

    pages.members._workers[-1].result.emit([{"uid": 2, "permission": 3}])

    assert pages.ctx.is_admin()
    assert not pages.overview._edit_btn.isHidden()
    assert pages.files._upload_btn.isEnabled()
    assert pages.strings._create_btn.isEnabled()
    assert pages.export._download_btn.isEnabled()
    assert pages.members._add_btn.isEnabled()
    assert counts == [len(page._workers) for page in (pages.files, pages.strings, pages.overview, pages.export)]
    pages.ctx.current_user = {"id": 3}
    assert pages.overview._edit_btn.isHidden()
    assert not pages.strings._create_btn.isEnabled()
    assert not pages.export._download_btn.isEnabled()


def test_permission_refresh_preserves_busy_action_gates(pages):
    pages.files._show_progress("busy")
    pages.export._set_busy(True)
    pages.members._workers[-1].result.emit([{"uid": 2, "permission": 3}])
    assert not pages.files._upload_btn.isEnabled()
    assert not pages.export._download_btn.isEnabled()
    pages.files._hide_progress()
    pages.export._set_busy(False)
    assert pages.files._upload_btn.isEnabled()
    assert pages.export._download_btn.isEnabled()


def test_late_members_cannot_grant_permissions_to_another_project(pages):
    old_worker = pages.members._workers[-1]
    pages.ctx.current_project = {"id": 8, "uid": 99, "name": "Another", "download": 2}
    old_worker.result.emit([{"uid": 2, "permission": 3}])
    assert not pages.ctx.is_admin()
    assert not pages.strings._create_btn.isEnabled()
    assert "_members" not in pages.ctx.current_project
