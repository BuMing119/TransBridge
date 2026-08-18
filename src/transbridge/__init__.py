"""TransBridge public package surface.

Importing the package must stay headless: GUI and MCP dependencies are loaded
only by their explicit entry points.
"""

from ._version import __version__

__all__ = ["__version__", "main"]


def main() -> int:
    """Compatibility facade for the historical ``transbridge:main`` target."""
    from .cli import main as cli_main

    return cli_main()
