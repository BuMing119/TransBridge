"""Name publication must preserve source spelling through the shared term guard."""

from tests.ui.tools import test_ai_project_name_preparation as support

name_run = support.name_run


def test_name_with_leading_space_does_not_abort_other_sources(name_run):
    run = name_run
    run.provider.translations[" Frost Immunity"] = "寒霜免疫"
    rows = [("name", " Frost Immunity", "MGEF:FULL")]
    first = run.task("A", rows + [("body", "Continue.", "BOOK:DESC")])
    second = run.task("B", rows + [("place", "Whiterun", "LCTN:FULL")])

    run.executor.prepare_names((first, second))

    assert run.provider.counts() == {" Frost Immunity": 1, "Whiterun": 1}
    assert {item.term: item.translation for item in run.executor.shared_term_pool.snapshot().entries} == {
        " Frost Immunity": "寒霜免疫",
        "Whiterun": "雪漫城",
    }
    outcomes = tuple(run.executor.execute(task) for task in (first, second))
    outcomes = run.executor.close_terms(outcomes)
    assert all(outcome.successful for outcome in outcomes), [
        (outcome.failed_keys, outcome.diagnostics, getattr(outcome.translation, "entry_outcomes", None))
        for outcome in outcomes
    ]
    assert run.provider.counts() == {" Frost Immunity": 1, "Whiterun": 1, "Continue.": 1}
    for task in (first, second):
        current = task.collection.get(task.entries[0].identity)
        assert current.original == " Frost Immunity"
        assert current.translation == "寒霜免疫"
    run.guard.require_current(run.request)
