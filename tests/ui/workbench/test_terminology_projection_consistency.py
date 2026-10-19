from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.workbench.terminology_async_support import wait_preview, wait_profile
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileEntryOverride,
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
    TerminologyProfileWriteProjectionSource,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar
from transbridge.ui.workbench.terminology_profile_controller import TerminologyProfileUiController
from transbridge.ui.workbench.translation_table import COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


def _base(*, extra: bool = True) -> EffectiveTerminologySnapshot:
    names = ("Whiterun", "Solitude") if extra else ("Whiterun",)
    return EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "base-1",
        "b" * 64,
        tuple(
            TermDecision(
                name,
                "project",
                "variant",
                name,
                name.casefold(),
                "Old" + name,
                TermScope.project(),
                DecisionStatus.ADOPTED,
            )
            for name in names
        ),
    )


@pytest.mark.parametrize("base_state", ["ready", "absent", "no_version", "unavailable", "corrupt"])
@pytest.mark.parametrize("override", [False, True])
def test_preview_and_write_share_fallback_diagnostics_and_override_priority(monkeypatch, base_state, override) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    entry = TranslationEntry("e", "e", "Whiterun and Solitude", "OldWhiterun and OldSolitude", 1, None)
    content = TerminologyProfileContent(
        mappings=(ProfileTermMapping("Whiterun", "NewWhiterun", "OldWhiterun"),),
        overrides=(ProfileEntryOverride(entry.identity.serialize(), "Complete override"),) if override else (),
    )
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    profile = service.create_with_content("project", "Names", content)
    published = service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project", "variant", profile.profile_id)
    base = _base()
    if base_state == "absent":
        base = None
    elif base_state != "ready":
        statuses = {
            "no_version": EffectiveSnapshotStatus.NO_PROJECT_VERSION,
            "unavailable": EffectiveSnapshotStatus.UNAVAILABLE,
            "corrupt": EffectiveSnapshotStatus.CORRUPT,
        }
        base = EffectiveTerminologySnapshot("project", "variant", statuses[base_state], diagnostics=("test",))
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.refresh(TranslationEntryCollection((entry,)))
    widget.set_terminology_profile(published, base_snapshot=base)
    wait_preview(widget)
    frozen = TerminologyProfileWriteProjectionSource(
        lambda _project: service, base_snapshot_for=lambda _project, _variant: base
    ).freeze("project", "variant")
    written, diagnostics = frozen.project_entries((entry,))
    preview = widget._terminology_projection[entry.identity.serialize()]

    expected = "Complete override" if override else entry.translation
    assert preview.translation == written[0].translation == expected
    assert widget._table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == expected
    assert preview.diagnostics == diagnostics
    assert preview.used_override == override
    assert bool(diagnostics) is not override
    assert entry.translation == "OldWhiterun and OldSolitude"
    widget.close()


def test_controller_supplies_base_and_refreshes_unchanged_profile_when_base_changes(monkeypatch) -> None:
    from PyQt6.QtCore import QObject, pyqtSignal

    class Context(QObject):
        project_changed = pyqtSignal()
        variant_changed = pyqtSignal(str)
        active_version_identity = ("project", "variant")

    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    profile = service.create_with_content(
        "project",
        "Names",
        TerminologyProfileContent((ProfileTermMapping("Whiterun", "NewWhiterun", "OldWhiterun"),)),
    )
    service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project", "variant", profile.profile_id)
    base = _base(extra=False)
    factory = SimpleNamespace(
        profile_service_for=lambda _project: service,
        base_terminology_snapshot=lambda _project, _variant: base,
    )
    entry = TranslationEntry("e", "e", "Whiterun and Solitude", "OldWhiterun and OldSolitude", 1, None)
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.refresh(TranslationEntryCollection((entry,)))
    bar = TerminologyProfileBar()
    controller = TerminologyProfileUiController(Context(), factory, bar, widget)
    wait_profile(controller)
    wait_preview(widget)
    assert widget._terminology_projection[entry.identity.serialize()].translation == "NewWhiterun and OldSolitude"

    base = replace(_base(), version_id="base-2", content_digest="c" * 64)
    controller.refresh()
    wait_profile(controller)
    wait_preview(widget)

    result = widget._terminology_projection[entry.identity.serialize()]
    assert result.translation == entry.translation
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["profile_mapping_missing"]
    widget.close()
    bar.close()
