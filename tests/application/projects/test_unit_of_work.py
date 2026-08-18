from __future__ import annotations

import pytest

from transbridge.application.projects.models import LifecycleActivation
from transbridge.persistence.project_lifecycle_uow import RepositoryLifecycleUnitOfWorkFactory


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_commit = False

    def begin(self, transaction_id: str) -> None:
        self.calls.append(("begin", transaction_id))

    def stage_save(self, transaction_id: str, _save) -> None:
        self.calls.append(("save", transaction_id))

    def stage_activate(self, transaction_id: str, _activation) -> None:
        self.calls.append(("activate", transaction_id))

    def stage_snapshot(self, transaction_id: str, _snapshot) -> None:
        self.calls.append(("snapshot", transaction_id))

    def commit(self, transaction_id: str) -> None:
        self.calls.append(("commit", transaction_id))
        if self.fail_commit:
            raise OSError("injected")

    def rollback(self, transaction_id: str) -> None:
        self.calls.append(("rollback", transaction_id))


def test_uow_allows_one_aggregate_mutation_and_one_commit() -> None:
    store = _Store()
    uow = RepositoryLifecycleUnitOfWorkFactory(store, lambda: "tx-1").begin()
    uow.stage_activate(LifecycleActivation(None, None, None, None, None, None, None, None))

    with pytest.raises(RuntimeError, match="exactly one"):
        uow.stage_activate(LifecycleActivation(None, None, None, None, None, None, None, None))

    uow.commit()
    with pytest.raises(RuntimeError, match="already finished"):
        uow.commit()
    assert store.calls == [("begin", "tx-1"), ("activate", "tx-1"), ("commit", "tx-1")]


def test_failed_store_commit_can_be_rolled_back() -> None:
    store = _Store()
    store.fail_commit = True
    uow = RepositoryLifecycleUnitOfWorkFactory(store, lambda: "tx-2").begin()
    uow.stage_activate(LifecycleActivation(None, None, None, None, None, None, None, None))

    with pytest.raises(OSError):
        uow.commit()
    uow.rollback()

    assert store.calls[-2:] == [("commit", "tx-2"), ("rollback", "tx-2")]
