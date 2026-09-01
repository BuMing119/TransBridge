from dataclasses import replace
import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

from tests.contracts.terminology.test_repository_contract import _build
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.models import ConflictGroup, ConflictVariant
from transbridge.persistence.terminology import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def _settle(model):
    deadline = time.monotonic() + 2
    while model.is_loading and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert not model.is_loading


def test_conflict_filter_button_applies_search_and_risk_to_sqlite(tmp_path):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    conflicts = tuple(
        ConflictGroup(
            f"conflict-{number}",
            "project-1",
            "variant-1",
            original,
            (
                ConflictVariant("龙", ("candidate-1",), ("evidence-1",)),
                ConflictVariant("巨龙", ("candidate-2",), ("evidence-2",)),
            ),
            risk=risk,
        )
        for number, original, risk in ((1, "dragon", "high"), (2, "sword", "medium"))
    )
    build = replace(_build(), conflicts=conflicts)
    repository.put_build(build)
    window = TerminologyWindow(
        TerminologyPresenter(
            TerminologyUiServices(queries=repository),
            RequestContext("owner", project_id="project-1", variant_id="variant-1"),
        )
    )
    try:
        window.bind_page("conflicts", build.ref)
        _settle(window.conflicts_model)
        window.conflicts_view.search.setText("DRAGON")
        window.conflicts_view.risk.setCurrentIndex(window.conflicts_view.risk.findData("high"))
        window.conflicts_view.apply_filter.click()
        _settle(window.conflicts_model)
        assert window.conflicts_model.rowCount() == 1
        assert window.conflicts_model.index(0, 0).data() == "dragon"
        window.bind_page("conflicts", build.ref)
        _settle(window.conflicts_model)
        assert window.conflicts_model.rowCount() == 1

        window.conflicts_view.search.setText("does-not-exist")
        window.conflicts_view.apply_filter.click()
        _settle(window.conflicts_model)
        assert window.conflicts_model.rowCount() == 0

        window.conflicts_view.search.setText("巨龙")
        window.conflicts_view.risk.setCurrentIndex(0)
        window.conflicts_view.apply_filter.click()
        _settle(window.conflicts_model)
        assert window.conflicts_model.rowCount() == 2
    finally:
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)
        repository.close()
