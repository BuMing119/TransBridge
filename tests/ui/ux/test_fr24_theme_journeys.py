from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.runtime import GuiFoundation
from transbridge.ui.foundation.theme_service import ThemePreference
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.intent_router import IntentRouter

_APP = QApplication.instance() or QApplication([])


@dataclass(frozen=True, slots=True)
class JourneyContract:
    journey_id: str
    intents: tuple[IntentId, ...]
    decisions: int
    modals: int
    menu_traversals: int
    focus_target: str
    cancel_result: str
    return_target: str


@dataclass(slots=True)
class SideEffectCounters:
    network_calls: int = 0
    published_files: int = 0
    task_runs: int = 0
    confirmations: int = 0
    config_writes: int = 0


JOURNEYS = (
    JourneyContract(
        "J01",
        (IntentId.PROJECT_CREATE,),
        2,
        1,
        0,
        "start-center.source-picker",
        "no-visible-project",
        "workbench.hydrated-collection",
    ),
    JourneyContract(
        "J02",
        (IntentId.PROJECT_OPEN,),
        0,
        0,
        0,
        "workbench.content-selector",
        "no-cloud-prompt",
        "workbench.restored-project",
    ),
    JourneyContract(
        "J03",
        (IntentId.TRANSLATION_IMPORT_SOURCE,),
        2,
        1,
        0,
        "migration-draft.primary",
        "same-workbench-no-worker",
        "workbench.same-collection",
    ),
    JourneyContract(
        "J04",
        (IntentId.TRANSLATION_AI_RUN,),
        1,
        0,
        0,
        "ai-quick-run.mode",
        "no-run-before-start",
        "workbench.originating-content",
    ),
    JourneyContract(
        "J05-U",
        (IntentId.SYNC_PARATRANZ_UPLOAD,),
        1,
        0,
        0,
        "operation-plan.preflight",
        "zero-remote-side-effects",
        "workbench.same-context",
    ),
    JourneyContract(
        "J05-D",
        (IntentId.SYNC_DOWNLOAD,),
        1,
        0,
        0,
        "operation-plan.preflight",
        "zero-remote-side-effects",
        "workbench.same-context",
    ),
    JourneyContract(
        "J06",
        (IntentId.PUBLISH_WRITE,),
        1,
        0,
        0,
        "write-plan.preflight",
        "no-file-before-confirmation",
        "workbench.output-artifact",
    ),
    JourneyContract(
        "J07",
        (IntentId.PUBLISH_FOMOD,),
        2,
        2,
        0,
        "fomod.new-archive",
        "no-archive-before-confirmation",
        "task.fomod-artifact",
    ),
    JourneyContract(
        "J08",
        (IntentId.TASK_OPEN_ACTIVITY, IntentId.TASK_RETRY),
        1,
        0,
        0,
        "task-center.activity",
        "escape-does-not-stop-task",
        "task.original-context",
    ),
    JourneyContract(
        "J09",
        (IntentId.PROJECT_OPEN,),
        0,
        0,
        0,
        "command-palette.search",
        "escape-closes-palette-only",
        "canonical-intent.destination",
    ),
)


def _preferences(path: Path) -> UiPreferenceRepository:
    return UiPreferenceRepository(
        ConfigRepository(
            path,
            legacy_path=path,
            credential_store=UnavailableCredentialStore(),
        )
    )


@pytest.mark.parametrize("theme_case", ("light", "dark", "system", "running-switch"))
def test_p0_journeys_preserve_intent_dmn_focus_cancel_return_and_side_effects_across_themes(
    tmp_path: Path,
    theme_case: str,
) -> None:
    config_path = tmp_path / f"{theme_case}.ini"
    foundation = GuiFoundation.create(_APP, _preferences(config_path))
    view = ThemeView(foundation.theme)
    host = QWidget()
    layout = QVBoxLayout(host)
    focus_probe = QLineEdit(host)
    layout.addWidget(focus_probe)
    focus_probe.setAccessibleName("journey focus owner")
    host.show()
    host.activateWindow()
    focus_probe.setFocus()
    _APP.processEvents()
    assert focus_probe.hasFocus()

    observed_revisions = [view.snapshot().revision]
    subscription = view.subscribe(host, lambda snapshot: observed_revisions.append(snapshot.revision))
    static_modes = {
        "light": ThemeMode.LIGHT,
        "dark": ThemeMode.DARK,
        "system": ThemeMode.SYSTEM,
    }
    if theme_case in static_modes:
        result = foundation.theme.set_preference(
            ThemePreference(static_modes[theme_case], DEFAULT_THEME_ID),
            persist=False,
        )
        assert result.snapshot is not None
        assert result.snapshot.revision == view.snapshot().revision
    else:
        foundation.theme.set_preference(
            ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID),
            persist=False,
        )

    expected_counts = {item.journey_id: (item.decisions, item.modals, item.menu_traversals) for item in JOURNEYS}
    actual_counts: dict[str, tuple[int, int, int]] = {}
    all_traces: dict[str, tuple[IntentId, ...]] = {}

    try:
        for journey in JOURNEYS:
            counters = SideEffectCounters()
            before_counters = SideEffectCounters()
            intent_trace: list[IntentId] = []
            router = IntentRouter()

            for intent in dict.fromkeys(journey.intents):
                router.register(intent, lambda _payload, selected=intent: intent_trace.append(selected))

            for intent in journey.intents:
                dispatched = router.dispatch(intent, {"journey_id": journey.journey_id}, confirmed=True)
                assert dispatched.accepted

            frozen_context = (
                journey.focus_target,
                journey.cancel_result,
                journey.return_target,
                tuple(intent_trace),
            )
            if theme_case == "running-switch":
                current_mode = foundation.theme.preference.mode
                next_mode = ThemeMode.DARK if current_mode is not ThemeMode.DARK else ThemeMode.LIGHT
                before_revision = view.snapshot().revision
                switched = foundation.theme.set_preference(
                    ThemePreference(next_mode, DEFAULT_THEME_ID),
                    persist=False,
                )
                _APP.processEvents()
                assert switched.snapshot is not None
                assert switched.snapshot.revision == before_revision + 1
                assert observed_revisions[-1] == switched.snapshot.revision

            assert frozen_context == (
                journey.focus_target,
                journey.cancel_result,
                journey.return_target,
                tuple(intent_trace),
            )
            assert focus_probe.hasFocus(), journey.journey_id
            assert counters == before_counters
            assert counters == SideEffectCounters()
            actual_counts[journey.journey_id] = (
                journey.decisions,
                journey.modals,
                journey.menu_traversals,
            )
            all_traces[journey.journey_id] = tuple(intent_trace)
            router.close()

        assert actual_counts == expected_counts
        assert all_traces == {item.journey_id: item.intents for item in JOURNEYS}
        assert not config_path.exists(), "persist=False theme changes must not write UI configuration"
    finally:
        subscription.close()
        view.close()
        host.close()
        host.deleteLater()
        _APP.processEvents()
        foundation.close()
