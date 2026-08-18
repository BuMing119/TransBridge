"""Framework-neutral Graph workload adapter for ``TaskRuntime`` backends."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import threading
from typing import Any, Protocol

from .checkpoint import CheckpointExpectation
from .controls import CancellationToken


class GraphExecutorPort(Protocol):
    def execute_graph(
        self,
        graph: Any,
        *,
        checkpoint_identity: CheckpointExpectation,
    ) -> list[Any]: ...

    def cancel(self) -> None: ...


class GraphWorkloadState(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class GraphWorkloadOutcome:
    state: GraphWorkloadState
    run_id: str
    results: tuple[Any, ...] = ()


class GraphWorkloadAdapter:
    """Consume cancellation and return an outcome without writing Job terminal state."""

    def __init__(
        self,
        executor: GraphExecutorPort,
        graph: Any,
        checkpoint_identity: CheckpointExpectation,
    ) -> None:
        self._executor = executor
        self._graph = graph
        self._identity = checkpoint_identity

    def __call__(self, cancellation: CancellationToken) -> GraphWorkloadOutcome:
        watcher_stop = threading.Event()

        def watch_cancellation() -> None:
            while not watcher_stop.is_set():
                if cancellation.wait(0.05):
                    self._executor.cancel()
                    return

        watcher = threading.Thread(
            target=watch_cancellation,
            name=f"graph-cancel-{self._identity.run_id}",
            daemon=True,
        )
        watcher.start()
        try:
            if cancellation.is_cancelled:
                self._executor.cancel()
                return GraphWorkloadOutcome(GraphWorkloadState.CANCELLED, self._identity.run_id)
            results = self._executor.execute_graph(
                self._graph,
                checkpoint_identity=self._identity,
            )
            if cancellation.is_cancelled:
                return GraphWorkloadOutcome(GraphWorkloadState.CANCELLED, self._identity.run_id)
            return GraphWorkloadOutcome(
                GraphWorkloadState.COMPLETED,
                self._identity.run_id,
                tuple(results),
            )
        finally:
            watcher_stop.set()
            watcher.join(timeout=0.2)
