from dataclasses import replace

import pytest

from tests.contracts.terminology.test_repository_contract import _build
from transbridge.application.terminology.conflict_queries import ConflictFilter
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.in_memory import InMemoryTerminologyRepository
from transbridge.application.terminology.models import ConflictGroup, ConflictVariant
from transbridge.application.terminology.ports import PageRequest
from transbridge.persistence.terminology import SqliteTerminologyRepository


@pytest.fixture(params=("memory", "sqlite"))
def repository(request, tmp_path):
    repository = (
        InMemoryTerminologyRepository()
        if request.param == "memory"
        else SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    )
    yield repository
    if request.param == "sqlite":
        repository.close()


def _conflicts_build():
    conflicts = tuple(
        ConflictGroup(
            f"conflict-{number}",
            "project-1",
            "variant-1",
            original,
            (
                ConflictVariant(translation, ("candidate-1",), ("evidence-1",)),
                ConflictVariant("另一译名", ("candidate-2",), ("evidence-2",)),
            ),
            risk=risk,
        )
        for number, original, translation, risk in (
            (1, "Straße", "街道", "high"),
            (2, "Straße", "道路", "medium"),
            (3, "street", "STRASSE", "high"),
            (4, "100%_literal", "字面量", "high"),
        )
    )
    return replace(_build(), conflicts=conflicts)


def test_filters_precede_paging_and_total_with_unicode_casefold(repository):
    build = _conflicts_build()
    repository.put_build(build)
    filters = ConflictFilter(search=" STRASSE ", risk="high")
    first = repository.list_conflicts(build.ref, PageRequest(limit=1), filters=filters)
    assert [item.conflict_group_id for item in first.items] == ["conflict-1"]
    assert first.total == 2
    assert first.next_cursor is not None
    second = repository.list_conflicts(build.ref, PageRequest(limit=1, cursor=first.next_cursor), filters=filters)
    assert [item.conflict_group_id for item in second.items] == ["conflict-3"]
    assert second.total == 2
    assert second.next_cursor is None

    for other_filter in (ConflictFilter("strasse", "medium"), ConflictFilter("street", "high"), ConflictFilter()):
        with pytest.raises(CursorStaleError):
            repository.list_conflicts(build.ref, PageRequest(cursor=first.next_cursor), filters=other_filter)


def test_search_treats_wildcards_as_literal_text_and_does_not_search_metadata(repository):
    build = _conflicts_build()
    repository.put_build(build)
    result = repository.list_conflicts(build.ref, filters=ConflictFilter("%_"))
    assert [item.conflict_group_id for item in result.items] == ["conflict-4"]
    assert result.total == 1
    empty = repository.list_conflicts(build.ref, filters=ConflictFilter("project-1"))
    assert empty.items == ()
    assert empty.total == 0
