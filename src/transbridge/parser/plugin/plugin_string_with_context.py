from dataclasses import dataclass

from .item import ContextUnion
from sse_plugin_interface.plugin_string import PluginString


@dataclass
class PluginStringWithContext(PluginString):
    """
    Dataclass for all strings that are extracted from a plugin.
    """

    context: ContextUnion | None = None
    """The context data for this string."""

    def __hash__(self) -> int:
        return hash((
            super().__hash__(),
            self.context.model_dump_json() if self.context else None,
        ))