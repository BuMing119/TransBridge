from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..execution_engine import StepResult


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    modified_args: dict | None = None
    modified_result: dict | None = None


class GuardMiddleware(ABC):
    @abstractmethod
    def before_execute(self, step: dict, ctx) -> GuardResult:
        ...

    @abstractmethod
    def after_execute(self, step: dict, result: StepResult, ctx) -> GuardResult:
        ...
