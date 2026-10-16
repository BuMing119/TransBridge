from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

from tests.application.terminology_sync import test_draft_import as inbound
from tests.ui.tools.terminology.test_sync_review_details import _item
from tests.ui.workbench.terminology_async_support import wait_until
from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import OwnerRef
from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemOutcome,
    TerminologySyncItemStatus,
)
from transbridge.application.terminology_sync.inbound import InboundReviewDecision
from transbridge.ui.tools.terminology import sync_presenter, sync_projection
from transbridge.ui.tools.terminology.inbound_review import InboundReviewEdit
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter, TerminologySyncViewState
from transbridge.ui.tools.terminology.sync_view import TerminologySyncPanel

_APP = QApplication.instance() or QApplication([])


def _presenter(service):
    return TerminologySyncPresenter(service, RequestContext("owner"), OwnerRef("owner", "gui"))


def test_outcomes_are_indexed_once_and_only_the_visible_page_is_formatted(monkeypatch):
    items = tuple(_item(i) for i in range(121))
    result = TerminologyBackupExecutionResult(
        "run",
        "hash",
        tuple(
            TerminologySyncItemOutcome(item.item_id, item.action, TerminologySyncItemStatus.SUCCEEDED, "OK", "done")
            for item in items
        ),
    )
    rows = []
    original = sync_projection.OutcomeProjection._row
    monkeypatch.setattr(
        sync_projection.OutcomeProjection, "_row", lambda self, item: rows.append(item.item_id) or original(self, item)
    )
    state = TerminologySyncViewState(result=result, result_items=items)
    assert rows == []
    panel = TerminologySyncPanel(_presenter(SimpleNamespace()))
    try:
        panel.render_sync(state)
        assert len(rows) == 50
        panel.result_details.table.selectRow(4)
        panel.render_sync(replace(state, busy=False))
        assert len(rows) == 50
        assert panel.result_details.table.currentRow() == 4
        panel.result_details.next_button.click()
        assert len(rows) == 100
        assert "Sword 50" in panel.result_details.table.item(0, 0).text()
        assert panel.result_details._rows is None
        assert len(panel.result_details._page) == 50
        replacement = replace(state, result=replace(result, run_id="other"))
        assert replacement.result_projection is not state.result_projection
        panel.render_sync(replacement)
        assert panel.result_details.range_label.text() == "1-50 / 121"
    finally:
        panel.dispose()


def _inbound_presenter(count=121):
    change_set = inbound._change_set(*(["add"] * count))
    importer, *_ = inbound._service(change_set)
    service = SimpleNamespace(
        list_inbound=lambda _context: (change_set,),
        prepare_import_selection=lambda _context, _set, choices: inbound._selection(change_set, choices),
        preview_import=importer.preview,
    )
    presenter = _presenter(service)
    presenter.load_inbound()
    return presenter, change_set


def test_inbound_page_change_reuses_preview_index_diagnostics_and_selection(monkeypatch):
    presenter, change_set = _inbound_presenter()
    choices = tuple(DraftImportChoice(item.item_id, InboundReviewDecision.ACCEPT) for item in change_set.items)
    state = presenter.preview_inbound(choices)
    rows = []
    original = sync_projection._proposal_row
    monkeypatch.setattr(sync_projection, "_proposal_row", lambda item: rows.append(item.item_id) or original(item))
    panel = TerminologySyncPanel(presenter)
    try:
        panel.render_sync(state)
        projection = state.inbound_projection
        diagnostics = panel.inbound_review.diagnostics
        revisions = []
        diagnostics.textChanged.connect(lambda: revisions.append(True))
        assert len(rows) == 50
        panel.inbound_review.page(50)
        assert presenter.state.inbound_projection is projection
        assert len(rows) == 50 and revisions == []
        panel.inbound_review.preview_details.next_button.click()
        assert len(rows) == 100
        panel.inbound_review.page(-50)
        assert len(rows) == 100 and revisions == []
        assert panel.inbound_review.preview_details.range_label.text() == "51-100 / 121"
        panel.inbound_table.cellWidget(0, 3).setText("edited")
        assert presenter.state.inbound_projection is None
    finally:
        panel.dispose()


def test_preview_freezes_raw_edits_before_worker_conversion_and_rejects_stale_request(monkeypatch):
    presenter, change_set = _inbound_presenter(1)
    panel = TerminologySyncPanel(presenter)
    queued, conversions = [], []
    panel.inbound_review._run = lambda _name, command: queued.append(command)
    original = InboundReviewEdit.to_choice
    monkeypatch.setattr(InboundReviewEdit, "to_choice", lambda edit: conversions.append(get_ident()) or original(edit))
    try:
        panel.render_sync(presenter.state)
        combo = panel.inbound_table.cellWidget(0, 2)
        combo.setCurrentIndex(combo.findData("edit"))
        panel.inbound_table.cellWidget(0, 3).setText("captured")
        panel.inbound_review.preview()
        assert conversions == []
        # Simulate a UI edit after queuing: the captured request must become stale.
        panel.inbound_table.cellWidget(0, 3).setText("replacement")
        queued.pop()()
        assert conversions == [] and presenter.state.inbound_proposal is None
        panel.inbound_review.preview()
        request = queued.pop()
        from transbridge.ui.tools.terminology.sync_dispatch import SyncCall

        pool = QThreadPool()
        pool.start(SyncCall(request, panel._signals))
        wait_until(lambda: presenter.state.inbound_proposal is not None)
        assert pool.waitForDone(3000)
        assert conversions and get_ident() not in conversions
        choices = presenter.state.inbound_proposal.selection.choices
        assert choices[0].decision is InboundReviewDecision.EDIT
        assert choices[0].edited.translation == "replacement"
        assert choices[0].item_id == change_set.items[0].item_id
    finally:
        panel.dispose()


def test_generation_is_rechecked_after_expensive_proposal_index_creation(monkeypatch):
    presenter, change_set = _inbound_presenter(1)
    original = sync_presenter.ProposalProjection

    def changed_while_indexing(proposal):
        result = original(proposal)
        presenter.invalidate_inbound_preview()
        return result

    monkeypatch.setattr(sync_presenter, "ProposalProjection", changed_while_indexing)
    choices = tuple(DraftImportChoice(item.item_id, InboundReviewDecision.ACCEPT) for item in change_set.items)
    state = presenter.preview_inbound(choices)
    assert state.inbound_proposal is None
    assert state.inbound_projection is None
