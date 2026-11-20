from __future__ import annotations

import pytest

from transbridge.ui.guidance import (
    GuidanceContextIdentity,
    GuidanceIntentId,
    GuidanceKind,
    GuidanceMode,
    GuidanceProjection,
    build_guidance_state,
    present_guidance,
)


@pytest.mark.parametrize(
    ("kind", "primary", "recovery"),
    (
        (
            GuidanceKind.NO_PROJECT,
            GuidanceIntentId.PROJECT_CREATE,
            GuidanceIntentId.PROJECT_OPEN,
        ),
        (
            GuidanceKind.EMPTY_PROJECT,
            GuidanceIntentId.WORKBENCH_CONTENT_PREPARE,
            GuidanceIntentId.PROJECT_CREATE,
        ),
        (
            GuidanceKind.UNTRANSLATED,
            GuidanceIntentId.TRANSLATION_AI_RUN,
            GuidanceIntentId.TRANSLATION_IMPORT_SOURCE,
        ),
        (
            GuidanceKind.REVIEW_PENDING,
            GuidanceIntentId.TRANSLATION_REVIEW,
            GuidanceIntentId.TASK_OPEN_ACTIVITY,
        ),
        (
            GuidanceKind.PUBLISH_PENDING,
            GuidanceIntentId.PUBLISH_WRITE,
            GuidanceIntentId.SYNC_PARATRANZ_UPLOAD,
        ),
        (
            GuidanceKind.MISSING_CONFIGURATION,
            GuidanceIntentId.SETTINGS_SERVICES,
            GuidanceIntentId.TRANSLATION_IMPORT_SOURCE,
        ),
        (GuidanceKind.FAILED, GuidanceIntentId.TASK_RETRY, GuidanceIntentId.TASK_OPEN_ACTIVITY),
        (
            GuidanceKind.PARTIAL_FAILURE,
            GuidanceIntentId.TASK_RETRY,
            GuidanceIntentId.TASK_OPEN_ACTIVITY,
        ),
    ),
)
def test_guidance_matrix_has_one_stable_primary_and_recovery(
    kind: GuidanceKind,
    primary: GuidanceIntentId,
    recovery: GuidanceIntentId,
) -> None:
    projection = GuidanceProjection(
        context_identity=GuidanceContextIdentity(project_id="project-1"),
        generation=3,
        revision=8,
        kind=kind,
        missing_configuration=("LLM API",) if kind is GuidanceKind.MISSING_CONFIGURATION else (),
        retry_available=kind in {GuidanceKind.FAILED, GuidanceKind.PARTIAL_FAILURE},
    )

    state = build_guidance_state(projection)

    assert state.kind is kind
    assert state.primary_intent.intent_id is primary
    assert len(state.recovery_intents) == 1
    assert state.recovery_intents[0].intent_id is recovery
    assert state.headline and state.reason


@pytest.mark.parametrize("kind", (GuidanceKind.FAILED, GuidanceKind.PARTIAL_FAILURE))
def test_failure_without_retry_evidence_never_invents_retry(kind: GuidanceKind) -> None:
    state = build_guidance_state(
        GuidanceProjection(
            context_identity=GuidanceContextIdentity(run_id="run-1"),
            generation=1,
            revision=1,
            kind=kind,
            retry_available=False,
        )
    )

    assert state.primary_intent.intent_id is GuidanceIntentId.TASK_OPEN_ACTIVITY
    assert GuidanceIntentId.TASK_RETRY not in {item.intent_id for item in state.recovery_intents}
    assert "不会显示虚假的重试动作" in state.detail_lines[0]


def test_missing_configuration_lists_all_gaps_and_requires_them() -> None:
    state = build_guidance_state(
        GuidanceProjection(
            context_identity=GuidanceContextIdentity(project_id="project-1"),
            generation=1,
            revision=1,
            kind=GuidanceKind.MISSING_CONFIGURATION,
            missing_configuration=("LLM API", "模型"),
        )
    )
    assert "LLM API、模型" in state.reason

    with pytest.raises(ValueError, match="at least one missing item"):
        GuidanceProjection(
            context_identity=GuidanceContextIdentity(project_id="project-1"),
            generation=1,
            revision=1,
            kind=GuidanceKind.MISSING_CONFIGURATION,
        )


@pytest.mark.parametrize(
    ("kind", "show_auto"),
    [
        (GuidanceKind.NO_PROJECT, False),
        (GuidanceKind.EMPTY_PROJECT, False),
        (GuidanceKind.LOADING_CONTENT, False),
        (GuidanceKind.UNTRANSLATED, False),
        (GuidanceKind.REVIEW_PENDING, False),
        (GuidanceKind.PUBLISH_PENDING, False),
        (GuidanceKind.MISSING_CONFIGURATION, True),
        (GuidanceKind.FAILED, True),
        (GuidanceKind.PARTIAL_FAILURE, True),
    ],
)
def test_auto_only_shows_actionable_guidance_while_explicit_modes_keep_their_banner(kind, show_auto) -> None:
    state = build_guidance_state(
        GuidanceProjection(
            GuidanceContextIdentity(),
            0,
            0,
            kind,
            missing_configuration=("AI 服务",) if kind is GuidanceKind.MISSING_CONFIGURATION else (),
        )
    )
    auto = present_guidance(state, GuidanceMode.AUTO)
    assert auto.show_banner == show_auto
    for mode in (GuidanceMode.GUIDED, GuidanceMode.COMPACT):
        explicit = present_guidance(state, mode)
        assert explicit.show_banner
        assert explicit.command_signature == auto.command_signature
