"""Object-oriented project terminology workbench."""

from typing import TYPE_CHECKING

from .paged_models import KeysetPagedTableModel, PagedColumn, PageQuery
from .presenter import TerminologyCommandPort, TerminologyPresenter, TerminologyUiServices
from .task_adapter import TerminologyTaskAdapter, TerminologyTaskViewState
from .view_models import (
    TERMINOLOGY_AREAS,
    TechnicalDetail,
    TerminologyArea,
    TerminologyNotice,
    TerminologyPreflightViewState,
    TerminologySummaryViewState,
    business_diagnostic,
    phase_label,
)

if TYPE_CHECKING:
    from .window import TerminologyLauncher, TerminologyWindow


def __getattr__(name: str):
    # Reusable models/search must not import the window, which also hosts the
    # profile dialogs. Resolve entry points only when a caller opens the tool.
    if name in {"TerminologyLauncher", "TerminologyWindow"}:
        from .window import TerminologyLauncher, TerminologyWindow

        value = {"TerminologyLauncher": TerminologyLauncher, "TerminologyWindow": TerminologyWindow}[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TERMINOLOGY_AREAS",
    "KeysetPagedTableModel",
    "PageQuery",
    "PagedColumn",
    "TechnicalDetail",
    "TerminologyCommandPort",
    "TerminologyLauncher",
    "TerminologyNotice",
    "TerminologyPreflightViewState",
    "TerminologyPresenter",
    "TerminologySummaryViewState",
    "TerminologyTaskAdapter",
    "TerminologyTaskViewState",
    "TerminologyUiServices",
    "TerminologyWindow",
    "TerminologyArea",
    "business_diagnostic",
    "phase_label",
]
