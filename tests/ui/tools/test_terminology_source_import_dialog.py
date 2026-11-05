from __future__ import annotations

from datetime import UTC, datetime
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel
import pytest

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileImportPolicy,
    TerminologyProfileImportService,
    TerminologyProfileService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.ui.tools.terminology_profiles import TerminologyImportPolicyDialog, TerminologySourceImportDialog

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("policy", tuple(TerminologyProfileImportPolicy))
def test_import_dialog_explains_full_import_and_freezes_policy(policy) -> None:
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
    preview = TerminologyProfileImportService(profile_service).preview(
        "project-1", "variant-1", base, source, policy=policy
    )

    dialog = TerminologySourceImportDialog(preview, "社区方案")

    assert dialog.profile_name == "社区方案"
    assert not dialog.select_after_create
    assert any("独立副本" in label.text() for label in dialog.findChildren(QLabel))
    assert "新增 1 条术语" in dialog.summary_label.text()
    overwrite_count = 1 if policy is TerminologyProfileImportPolicy.USE_IMPORTED else 0
    assert f"覆盖已有译名 {overwrite_count} 条" in dialog.summary_label.text()
    rows = {dialog.table.model().index(row, 0).data(): row for row in range(2)}
    expected = "白漫城" if policy is TerminologyProfileImportPolicy.USE_IMPORTED else "雪漫城"
    assert dialog.table.model().index(rows["Whiterun"], 2).data() == expected
    assert dialog.table.model().index(rows["Riverwood"], 2).data() == "河木镇"
    assert dialog.table.model().index(rows["Riverwood"], 4).data() == "新增方案术语"
    assert not dialog.table.model().flags(dialog.table.model().index(0, 2)) & Qt.ItemFlag.ItemIsEditable
    policy_label = "保留已有译名" if policy is TerminologyProfileImportPolicy.KEEP_EXISTING else "采用导入译名"
    assert any(
        label.accessibleName() == "本次导入的批量处理方式" and label.text() == policy_label
        for label in dialog.findChildren(QLabel)
    )
    assert dialog.table.model().rowCount() == 2
    dialog.close()
    adoption = TerminologySourceImportDialog(preview, "社区术语", select_after_create=True)
    assert adoption.select_after_create
    assert adoption.create_button.text() == "创建并选用"
    adoption.select_check.setChecked(False)
    assert not adoption.select_after_create
    assert adoption.create_button.text() == "创建译名方案"
    adoption.close()
    _APP.processEvents()


def test_policy_dialog_selects_only_two_batch_policies_for_the_chosen_file():
    dialog = TerminologyImportPolicyDialog(
        SimpleNamespace(request=SimpleNamespace(file_path="D:/chosen/terms.csv"), label="terms.csv")
    )
    assert dialog.source_edit.text() == "terms.csv"
    assert dialog.source_edit.toolTip() == "D:/chosen/terms.csv"
    assert dialog.source_edit.isReadOnly()
    assert dialog.policy_combo.count() == 2
    assert dialog.policy_combo.currentText() == "保留已有译名"
    assert dialog.policy is TerminologyProfileImportPolicy.KEEP_EXISTING
    dialog.policy_combo.setCurrentIndex(1)
    assert dialog.policy_combo.currentText() == "采用导入译名"
    assert dialog.policy is TerminologyProfileImportPolicy.USE_IMPORTED
    dialog.close()
