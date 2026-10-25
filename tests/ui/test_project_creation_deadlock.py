"""The real background creation entry must not wait for its own GUI handoff."""

from dataclasses import replace
from time import monotonic

from PyQt6.QtTest import QTest
import pytest

from tests.plugin_fixtures import write_plugin
from tests.ui.test_project_creation_transition import _APP, creation as transition_creation  # noqa: F401
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase


@pytest.fixture
def creation(transition_creation):  # noqa: F811 - imported pytest fixture
    case = transition_creation
    try:
        yield case
    finally:
        # The broken worker waits for the GUI transaction that only its own
        # completion can finish. Release it before the reused fixture joins.
        binding = case.context._project_view
        if binding.opening:
            case.context.finish_project_open(binding._token)
        for worker in case.host._workers:
            assert worker.wait(5_000), "创建 worker 未在清理后结束"
        _APP.processEvents()
        case.context.close_projection()


def _wait_for_creation(case):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        if not case.controller.guided_project.state.in_flight and case.host._foreground_worker is None:
            return case.controller.guided_project.state
        QTest.qWait(10)
    state = case.controller.guided_project.state
    pytest.fail(
        f"真实后台新建入口超过有界等待：phase={state.phase.value}, "
        f"opening={case.context.project_opening}, worker={case.host._foreground_worker is not None}"
    )


def _create(case, name, paths=()):
    case.controller.guided_project.begin_sources(tuple(str(path) for path in paths))
    case.controller.guided_project.set_project_name(name)
    case.view._draft_primary.click()
    return _wait_for_creation(case)


def _plugins(case, count, prefix):
    return tuple(
        write_plugin(case.root / f"{prefix}{index}.esp", [(0x800 + index, f"NPC{index}", f"原文 {index}")])
        for index in range(count)
    )


def _assert_installed(case, paths):
    assert not case.context.project_opening
    assert not case.context.project_view_failed
    assert not case.context.authoritative_projection_diverged()
    assert set(case.context.slots) == {str(path.resolve()) for path in paths}
    assert sum(len(slot.collection) for slot in case.context.slots.values()) == len(paths)
    active = case.services.project_lifecycle.active
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()


@pytest.mark.parametrize("source_count", [0, 1, 80])
def test_first_background_creation_from_empty_context_finishes(creation, source_count):
    assert creation.context.active_project_id is None
    paths = _plugins(creation, source_count, "First")

    state = _create(creation, "第一次新建", paths)

    assert state.phase is GuidedDraftPhase.COMPLETED
    _assert_installed(creation, paths)


def test_creating_again_after_a_successful_background_creation_replaces_the_installed_view(creation):
    first = _plugins(creation, 1, "First")
    assert _create(creation, "第一个工程", first).phase is GuidedDraftPhase.COMPLETED
    first_id = creation.context.active_project_id
    second = _plugins(creation, 2, "Second")

    state = _create(creation, "第二个工程", second)

    assert state.phase is GuidedDraftPhase.COMPLETED
    assert creation.context.active_project_id != first_id
    _assert_installed(creation, second)
    assert all(path.is_file() for path in first)


def test_genuine_uncommitted_old_view_is_rejected_and_can_retry_after_reconciling_it(creation):
    old = _plugins(creation, 1, "Old")
    assert _create(creation, "保留旧工程", old).phase is GuidedDraftPhase.COMPLETED
    original_id = creation.context.active_project_id
    original_collection = creation.context.collection
    original_entry = next(iter(original_collection))
    creation.context.collection = TranslationEntryCollection((
        replace(original_entry, translation="真正未提交的旧工程草稿", stage=9),
    ))
    old_slot = creation.context.active_slot
    new = _plugins(creation, 1, "New")

    state = _create(creation, "待创建工程", new)

    assert state.phase is GuidedDraftPhase.FAILED
    assert state.diagnostic_code == "PROJECTION_AUTHORITY_DIVERGED"
    assert creation.context.active_project_id == original_id
    assert creation.context.active_slot is old_slot
    assert next(iter(creation.context.collection)).translation == "真正未提交的旧工程草稿"
    assert not creation.context.project_opening
    creation.context.collection = original_collection
    creation.view._draft_primary.click()
    assert _wait_for_creation(creation).phase is GuidedDraftPhase.COMPLETED
    _assert_installed(creation, new)


def test_source_preparation_failure_can_retry_the_same_background_create_button(creation):
    path = creation.root / "Retry.esp"
    path.write_bytes(b"invalid plugin")

    state = _create(creation, "原窗口重试", (path,))

    assert state.phase is GuidedDraftPhase.FAILED
    assert creation.context.active_project_id is None
    assert not creation.context.project_opening
    write_plugin(path, [(0x800, "RetriedNPC", "修复后的原文")])
    creation.view._draft_primary.click()
    assert _wait_for_creation(creation).phase is GuidedDraftPhase.COMPLETED
    _assert_installed(creation, (path,))
