# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from dataclasses import dataclass, field

from sse_plugin_interface.plugin_string import PluginString

from .item import ContextUnion


@dataclass
class PluginStringWithContext(PluginString):
    """
    Dataclass for all strings that are extracted from a plugin.
    """

    context: ContextUnion | None = None
    """The context data for this string."""

    string_id: int | None = None
    """The integer string ID for localised plugins, or None for non-localised."""

    dsd_index: int | None = field(default=None, compare=False)
    """The DSD index when derived separately from the native index."""

    def __hash__(self) -> int:
        return hash((
            super().__hash__(),
            self.context.model_dump_json() if self.context else None,
            self.string_id,
        ))
