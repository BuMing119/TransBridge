from dataclasses import dataclass

from sse_plugin_interface.plugin_string import PluginString


@dataclass
class PluginStringWithContext(PluginString):
    """
    Dataclass for all strings that are extracted from a plugin.
    """

    npc_sex: str | None = None
    """The Npc's sex (if record is NPC_)."""

    npc_race: str | None = None
    """The Npc's race (if record is NPC_)."""

    npc_class: str | None = None
    """The Npc's class (if record is NPC_)."""

    # INFO dialogue context fields
    dialogue_topic: str | None = None
    """The dialogue topic name (from parent DIAL FULL)."""

    quest_formid: str | None = None
    """The quest FormID (from DIAL QUST reference)."""

    speaker_formid: str | None = None
    """The speaker NPC FormID (from INFO ANAM)."""

    emotion_type: int | None = None
    """The emotion type value (from INFO TRDT)."""

    response_note: str | None = None
    """The response designer note (from INFO NAM2)."""

    dial_formid: str | None = None
    """The parent DIAL record's FormID (for INFO records)."""

    def __hash__(self) -> int:
        return super().__hash__()
