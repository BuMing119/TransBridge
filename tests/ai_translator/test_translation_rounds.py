from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from tests.conftest import make_entry
from transbridge.ai_translator.batch_planner import Batch
from transbridge.ai_translator.translation_rounds import run_translation_rounds


def _batch(key, quest=None):
    return Batch([make_entry(key, original=key)], "对话", quest)


def _run(plan, operation, *, completed=(), stop=None):
    pause = Event()
    pause.set()
    run_translation_rounds(
        plan,
        completed_fingerprints=set(completed),
        operation=operation,
        max_workers=4,
        stop_event=stop or Event(),
        pause_event=pause,
        log=lambda _: None,
    )


def test_round_barriers_quest_successors_and_unknown_quest_overlap():
    plan = SimpleNamespace(
        round1=[_batch("name")],
        round2=[
            _batch("quest-first", "quest"),
            _batch("quest-next", "quest"),
            _batch("unknown-a"),
            _batch("unknown-b"),
            _batch("restored"),
        ],
        round3=[_batch("book")],
    )
    names_done, quest_done = Event(), Event()
    unknowns = Barrier(2)
    visited = []

    def operation(batch, _name):
        key = batch.entries[0].key
        if key == "name":
            names_done.set()
        else:
            assert names_done.is_set()
        if key == "quest-first":
            quest_done.set()  # Includes all mandatory extraction work.
        elif key == "quest-next":
            assert quest_done.is_set()
        elif key.startswith("unknown"):
            unknowns.wait(timeout=2)
        elif key == "book":
            assert {"quest-first", "quest-next", "unknown-a", "unknown-b"} <= set(visited)
        visited.append(key)

    _run(plan, operation, completed=[frozenset({"restored"})])
    assert set(visited) == {"name", "quest-first", "quest-next", "unknown-a", "unknown-b", "book"}


@pytest.mark.parametrize("failure", [False, True])
def test_stop_or_failure_does_not_start_a_later_round(failure):
    stop = Event()
    visited = []
    plan = SimpleNamespace(round1=[_batch("name")], round2=[_batch("dialogue")], round3=[_batch("book")])

    def operation(batch, _name):
        visited.append(batch.entries[0].key)
        if failure:
            raise ValueError("first round failed")
        stop.set()

    if failure:
        with pytest.raises(ValueError, match="first round failed"):
            _run(plan, operation, stop=stop)
    else:
        _run(plan, operation, stop=stop)
    assert visited == ["name"]
