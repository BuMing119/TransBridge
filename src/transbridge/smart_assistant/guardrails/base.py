from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..execution_engine import StepResult

if TYPE_CHECKING:
    from ..tools.types import ExecutionContext


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    modified_args: dict | None = None
    modified_result: dict | None = None
    requires_confirmation: str = ""
    code: str = ""
    json_pointer: str = ""
    validation_issues: list[dict[str, object]] | None = None
    # C28: dedicated field to distinguish hard blocks from confirmation-pending.
    # "" = no confirmation needed (hard block if allowed=False).
    # "write" = write-level confirmation pending.
    # "admin" = admin-level confirmation pending.


class GuardMiddleware(ABC):
    @abstractmethod
    def before_execute(self, step: dict, ctx: ExecutionContext) -> GuardResult:
        """工具执行前护栏检查。

        Args:
            step: 待执行的步骤描述
            ctx: 执行上下文（forward reference 到 tools.base.ExecutionContext）
        """
        ...

    @abstractmethod
    def after_execute(self, step: dict, result: StepResult, ctx: ExecutionContext) -> GuardResult:
        """工具执行后护栏检查。

        Args:
            step: 已执行的步骤描述
            result: 步骤执行结果
            ctx: 执行上下文（forward reference 到 tools.base.ExecutionContext）
        """
        ...
