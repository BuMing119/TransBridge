from datetime import UTC, datetime
import threading
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import OwnerRef
from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice
from transbridge.application.terminology_sync.inbound import InboundReviewDecision
from transbridge.application.terminology_sync.plan_models import TerminologyContentSummary
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter
from transbridge.ui.tools.terminology.sync_view import TerminologySyncPanel

_APP = QApplication.instance() or QApplication([])


class _InboundService:
    def __init__(self, count=1):
        content = TerminologyContentSummary("Dragon", "dragon", "龙", "global")
        self.items = tuple(SimpleNamespace(item_id=f"item-{i:03}", remote=content, local=None) for i in range(count))
        self.sets = (
            SimpleNamespace(change_set_id="set", created_at=datetime(2026, 8, 30, tzinfo=UTC), items=self.items),
        )
        self.committed = []

    def list_inbound(self, _context):
        return self.sets

    def prepare_import_selection(self, _context, change_set_id, choices):
        return SimpleNamespace(change_set_id=change_set_id, choices=choices)

    def preview_import(self, selection):
        return SimpleNamespace(selection=selection, committable=True)

    def commit_import(self, proposal, _context):
        self.committed.extend(proposal.selection.choices)
        return SimpleNamespace()


@pytest.fixture
def inbound_panel(request):
    service = _InboundService(getattr(request, "param", 1))
    presenter = TerminologySyncPresenter(service, RequestContext("owner"), OwnerRef("owner", "gui"))
    panel = TerminologySyncPanel(presenter)
    panel._run = lambda pending, call: panel._completed(call())
    panel.render_sync(presenter.load_inbound())
    yield panel, service
    panel.close()


def _choose(panel, decision, row=0):
    widget = panel.inbound_table.cellWidget(row, 2)
    widget.setCurrentIndex(widget.findData(decision))


def test_changing_review_after_preview_blocks_old_commit(inbound_panel):
    panel, service = inbound_panel
    panel.preview_inbound_button.click()
    assert panel.commit_inbound_button.isEnabled()
    _choose(panel, "reject")
    assert not panel.commit_inbound_button.isEnabled()
    with pytest.raises(RuntimeError, match="preview"):
        panel.presenter.commit_inbound()
    assert service.committed == []

    panel.preview_inbound_button.click()
    panel.commit_inbound_button.click()
    assert service.committed[0].decision.value == "reject"


def test_edited_translation_is_repreviewed_with_its_new_content_digest(inbound_panel):
    panel, service = inbound_panel
    panel.preview_inbound_button.click()
    _choose(panel, "edit")
    panel.inbound_table.cellWidget(0, 3).setText("巨龙")
    assert not panel.commit_inbound_button.isEnabled()
    panel._preview_inbound()
    panel.commit_inbound_button.click()
    assert service.committed[0].edited.translation == "巨龙"
    assert service.committed[0].edited.digest != service.items[0].remote.digest


@pytest.mark.parametrize("inbound_panel", [51], indirect=True)
def test_review_decisions_survive_page_changes_and_preview_includes_reviewed_pages(inbound_panel):
    panel, service = inbound_panel
    _choose(panel, "reject")
    panel.next_inbound_button.click()
    _choose(panel, "edit")
    panel.inbound_table.cellWidget(0, 3).setText("末页译名")
    panel.previous_inbound_button.click()
    assert panel.inbound_table.cellWidget(0, 2).currentData() == "reject"
    panel._preview_inbound()
    panel.commit_inbound_button.click()
    choices = {item.item_id: item for item in service.committed}
    assert choices["item-000"].decision.value == "reject"
    assert choices["item-050"].edited.translation == "末页译名"


def test_empty_edit_invalidates_preview_without_losing_typed_draft(inbound_panel):
    panel, service = inbound_panel
    _choose(panel, "edit")
    panel._preview_inbound()
    panel.inbound_table.cellWidget(0, 3).clear()
    assert not panel.commit_inbound_button.isEnabled()
    panel._preview_inbound()
    assert panel.presenter.state.inbound_proposal is None
    assert panel.inbound_table.cellWidget(0, 3).text() == ""
    assert service.committed == []


def test_late_preview_cannot_restore_proposal_after_new_edit(inbound_panel):
    panel, service = inbound_panel
    entered = threading.Event()
    release = threading.Event()
    preview = service.preview_import

    def delayed(selection):
        entered.set()
        assert release.wait(3)
        return preview(selection)

    service.preview_import = delayed
    choices = (DraftImportChoice("item-000", InboundReviewDecision.ACCEPT),)
    worker = threading.Thread(target=lambda: panel.presenter.preview_inbound(choices))
    try:
        worker.start()
        assert entered.wait(2)
        _choose(panel, "reject")
        release.set()
        worker.join(2)
        assert not worker.is_alive()
        assert panel.presenter.state.inbound_proposal is None
        with pytest.raises(RuntimeError, match="preview"):
            panel.presenter.commit_inbound()
    finally:
        release.set()
        worker.join(3)
