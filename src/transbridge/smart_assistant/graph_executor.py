# NOTE: GraphExecutor 当前全代码库零引用，ExecutionEngine 未继承此 ABC。
# 保留待决定去留（可能在未来的图执行重构中使用或移除）。
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
