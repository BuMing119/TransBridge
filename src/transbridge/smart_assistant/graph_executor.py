from abc import ABC, abstractmethod

from .execution_engine import StepResult
from .graph_types import GraphSpec


class GraphExecutor(ABC):
    @abstractmethod
    def execute_graph(self, graph: GraphSpec) -> list[StepResult]:
        ...

    @abstractmethod
    def cancel(self) -> None:
        ...

    @abstractmethod
    def pause(self) -> None:
        ...

    @abstractmethod
    def resume(self) -> None:
        ...
