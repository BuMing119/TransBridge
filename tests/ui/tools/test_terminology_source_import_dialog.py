from __future__ import annotations

from datetime import UTC, datetime
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileImportService,
    TerminologyProfileService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.ui.tools.terminology_profiles import TerminologySourceImportDialog

_APP = QApplication.instance() or QApplication([])


def test_import_dialog_explains_snapshot_and_defaults_to_not_switching() -> None:
    profile_service = TerminologyProfileService(
        InMemoryTerminologyProfileRepository(),
        now=lambda: datetime(2026, 9, 6, tzinfo=UTC),
    )
    decision = TermDecision(
        "term-1",
        "project-1",
        "variant-1",
        "Whiterun",
        "whiterun",
        "雪漫城",
        TermScope.project(),
        DecisionStatus.ADOPTED,
    )
    base = EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id="base-v1",
        content_digest="a" * 64,
        decisions=(decision,),
    )
    source = TerminologySourceSnapshot.capture(
        "json",
        "本地 JSON · terms.json",
        (TerminologySourceEntry("Whiterun", "白漫城"), TerminologySourceEntry("Riverwood", "河木镇")),
    )
    preview = TerminologyProfileImportService(profile_service).preview("project-1", "variant-1", base, source)

    dialog = TerminologySourceImportDialog(preview, "社区方案")

    assert dialog.profile_name == "社区方案"
    assert not dialog.select_after_create
    assert any("独立副本" in label.text() for label in dialog.findChildren(QLabel))
    assert "采用来源译名 1 个" in dialog.summary_label.text()
    assert "来源独有且未导入 1 个" in dialog.summary_label.text()
    assert dialog.table.item(0, 3).text() == "采用来源译名"
    dialog.close()
    _APP.processEvents()
