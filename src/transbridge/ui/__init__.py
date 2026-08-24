"""Public UI entrypoints without importing the application graph eagerly."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import main as main


def main(*args, **kwargs):
    """Load the executable adapter only when the GUI is actually started."""

    from .app import main as run

    return run(*args, **kwargs)


__all__ = ["main"]
