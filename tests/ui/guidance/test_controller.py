from __future__ import annotations

from dataclasses import dataclass

from transbridge.config.ui_preferences import (
    GuidanceMode,
    UiPreferenceSaveResult,
    UiPreferenceSnapshot,
)
from transbridge.ui.guidance import (
    GuidanceContextIdentity,
    GuidanceController,
    GuidanceIntentId,
    GuidanceKind,
    GuidanceProjection,
    GuidanceVisibility,
)


@dataclass
class PreferenceStub:
    mode: GuidanceMode = GuidanceMode.AUTO
    fail: bool = False
    revision: int = 1

    def load(self) -> UiPreferenceSnapshot:
        return UiPreferenceSnapshot(self.mode, self.revision)

    def save_guidance_mode(self, mode: GuidanceMode) -> UiPreferenceSaveResult:
        if self.fail:
            return UiPreferenceSaveResult(
                mode,
                False,
                diagnostic_code="injected_write_failure",
                message="not saved",
            )
        self.mode = mode
        self.revision += 1
        return UiPreferenceSaveResult(mode, True, UiPreferenceSnapshot(mode, self.revision))


def _projection(
    *,
    generation: int = 1,
    revision: int = 1,
    project_id: str = "project-1",
    kind: GuidanceKind = GuidanceKind.UNTRANSLATED,
) -> GuidanceProjection:
    return GuidanceProjection(
        context_identity=GuidanceContextIdentity(project_id=project_id),
        generation=generation,
        revision=revision,
        kind=kind,
    )


def test_duplicate_and_out_of_order_revisions_do_not_prompt_or_submit_twice() -> None:
    presentations = []
    commands = []
    controller = GuidanceController(presentations.append, lambda intent: commands.append(intent) or "ok")

    assert controller.project(_projection()).accepted
    assert not controller.project(_projection()).accepted
    assert not controller.project(_projection(revision=0)).accepted
    first = controller.submit_primary(expected_revision=1)
    duplicate = controller.submit_primary(expected_revision=1)

    assert len(presentations) == 1
    assert first.accepted and first.result == "ok"
    assert not duplicate.accepted and duplicate.reason == "duplicate"
    assert commands == [GuidanceIntentId.TRANSLATION_AI_RUN]


def test_new_generation_switches_context_and_rejects_late_updates() -> None:
    presentations = []
    controller = GuidanceController(presentations.append, lambda intent: intent)

    controller.project(_projection(generation=2, revision=5, project_id="old"))
    switched = controller.project(_projection(generation=3, revision=0, project_id="new"))
    late = controller.project(_projection(generation=2, revision=6, project_id="old"))
    wrong_identity = controller.project(_projection(generation=3, revision=1, project_id="other"))

    assert switched.accepted
    assert not late.accepted and late.reason == "stale_generation"
    assert not wrong_identity.accepted and wrong_identity.reason == "context_identity_mismatch"
    assert len(presentations) == 2
    assert presentations[-1].state.context_identity.project_id == "new"


def test_collapse_hide_restore_change_only_visibility() -> None:
    presentations = []
    controller = GuidanceController(presentations.append, lambda intent: intent)
    controller.project(_projection())
    original_signature = presentations[-1].command_signature

    assert controller.collapse()
    assert presentations[-1].visibility is GuidanceVisibility.COLLAPSED
    assert controller.hide()
    assert presentations[-1].visibility is GuidanceVisibility.HIDDEN
    assert controller.restore()
    assert presentations[-1].visibility is GuidanceVisibility.EXPANDED
    assert all(item.command_signature == original_signature for item in presentations)


def test_close_is_idempotent_and_blocks_late_projection_and_command() -> None:
    presentations = []
    commands = []
    controller = GuidanceController(presentations.append, commands.append)
    controller.project(_projection())
    controller.close()
    controller.close()

    reduction = controller.project(_projection(revision=2))
    submission = controller.submit_primary()

    assert controller.closed
    assert not reduction.accepted and reduction.reason == "closed"
    assert not submission.accepted
    assert len(presentations) == 1
    assert commands == []


def test_mode_write_failure_does_not_apply_or_claim_saved() -> None:
    preferences = PreferenceStub(GuidanceMode.GUIDED, fail=True)
    presentations = []
    controller = GuidanceController(presentations.append, lambda intent: intent, preferences=preferences)
    controller.project(_projection())

    result = controller.set_mode(GuidanceMode.COMPACT)

    assert not result.saved
    assert result.diagnostic_code == "injected_write_failure"
    assert controller.mode is GuidanceMode.GUIDED
    assert len(presentations) == 1


def test_mode_changes_only_after_successful_persistence() -> None:
    preferences = PreferenceStub(GuidanceMode.AUTO)
    presentations = []
    controller = GuidanceController(presentations.append, lambda intent: intent, preferences=preferences)
    controller.project(_projection())

    result = controller.set_mode(GuidanceMode.COMPACT)

    assert result.saved
    assert controller.mode is GuidanceMode.COMPACT
    assert preferences.mode is GuidanceMode.COMPACT
    assert presentations[-1].configured_mode is GuidanceMode.COMPACT
    assert presentations[-1].state == presentations[-2].state


def test_guided_and_compact_dispatch_identical_command_and_result() -> None:
    outputs = {}

    def exercise(mode: GuidanceMode):
        presentations = []
        dispatched = []
        controller = GuidanceController(
            presentations.append,
            lambda intent: dispatched.append(intent) or {"status": "created", "intent": intent.value},
            initial_mode=mode,
        )
        controller.project(_projection())
        result = controller.submit_primary()
        outputs[mode] = (presentations[-1], dispatched, result)

    exercise(GuidanceMode.GUIDED)
    exercise(GuidanceMode.COMPACT)
    guided, guided_commands, guided_result = outputs[GuidanceMode.GUIDED]
    compact, compact_commands, compact_result = outputs[GuidanceMode.COMPACT]

    assert guided.command_signature == compact.command_signature
    assert guided.state == compact.state
    assert guided_commands == compact_commands == [GuidanceIntentId.TRANSLATION_AI_RUN]
    assert guided_result.result == compact_result.result
    assert len(compact.explanation_lines) < len(guided.explanation_lines)
