"""
SSEPluginWithContext - Extended plugin parser with NPC context extraction.

This module extends the sse_plugin_interface to extract strings with additional
context information such as NPC sex, race, and class.
"""

import logging

from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.group import Group
from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import TRDT, StringSubrecord

from .plugin_string_with_context import PluginStringWithContext


class SSEPluginWithContext(SSEPlugin):
    """
    Extended SSE Plugin parser that extracts strings with NPC context.
    Based on sse_plugin_interface.SSEPlugin with additional context extraction.
    """

    __string_subrecords: dict[PluginStringWithContext, StringSubrecord] | None = None
    """Dictionary mapping extracted strings to their subrecords."""

    log: logging.Logger = logging.getLogger("SSEPluginWithContext")

    def __init__(self, name: str) -> None:
        """
        Args:
            name (str): The name of the plugin.
        """
        super().__init__(name)

    @property
    def plugin_name(self) -> str:
        """Returns the plugin name."""
        return self._SSEPlugin__plugin_name

    @property
    def groups(self) -> list[Group]:
        """Returns the list of groups."""
        return self._SSEPlugin__groups

    @property
    def masters(self) -> list[RawString]:
        """Returns the list of master plugins."""
        return self._SSEPlugin__masters

    # NPC Context extraction methods

    @staticmethod
    def _extract_npc_context(
        record: Record,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Extracts NPC context (sex, race, class) from an NPC_ record.

        Args:
            record (Record): The NPC_ record to extract context from.

        Returns:
            tuple: (npc_sex, npc_race, npc_class) - values or None if not found.
        """
        npc_sex: str | None = None
        npc_race: str | None = None
        npc_class: str | None = None

        for subrecord in record.subrecords:
            # ACBS - Actor Base Configuration, contains sex in flags
            if subrecord.type == "ACBS" and len(subrecord.data) >= 4:
                # Flags are in the first 4 bytes (uint32)
                flags = int.from_bytes(subrecord.data[:4], byteorder="little")
                # Bit 0 = Female flag
                npc_sex = "Female" if (flags & 0x01) else "Male"

            # RNAM - Race reference (FormID)
            elif subrecord.type == "RNAM" and len(subrecord.data) >= 4:
                # Read FormID as little-endian uint32, then convert to hex
                race_formid_int = int.from_bytes(subrecord.data[:4], byteorder="little")
                npc_race = hex(race_formid_int).removeprefix("0x").upper().zfill(8)

            # CNAM - Class reference (FormID)
            elif subrecord.type == "CNAM" and len(subrecord.data) >= 4:
                # Read FormID as little-endian uint32, then convert to hex
                class_formid_int = int.from_bytes(subrecord.data[:4], byteorder="little")
                npc_class = hex(class_formid_int).removeprefix("0x").upper().zfill(8)

        return npc_sex, npc_race, npc_class

    @staticmethod
    def _extract_info_context(
        record: Record,
    ) -> tuple[str | None, int | None, str | None]:
        """
        Extracts INFO dialogue context (speaker FormID, emotion type, response note).

        Args:
            record (Record): The INFO record to extract context from.

        Returns:
            tuple: (speaker_formid, emotion_type, response_note) - values or None if not found.
        """
        speaker_formid: str | None = None
        emotion_type: int | None = None
        response_note: str | None = None

        # Function indices for speaker detection
        # GetIsID = 72, GetInFaction = 71, GetIsRace = 68
        GETISID_FUNCTION = 72

        for subrecord in record.subrecords:
            # ANAM - Direct Speaker NPC FormID (fallback, not always present)
            if subrecord.type == "ANAM" and len(subrecord.data) >= 4:
                speaker_formid_int = int.from_bytes(subrecord.data[:4], byteorder="little")
                speaker_formid = hex(speaker_formid_int).removeprefix("0x").upper().zfill(8)

            # CTDA - Condition data, may contain GetIsID with speaker NPC
            elif subrecord.type == "CTDA" and len(subrecord.data) >= 20:
                # CTDA structure (32 bytes):
                # [0-3]: Flags/Type
                # [4-7]: Comparison value (float or int)
                # [8-9]: Function index (UInt16)
                # [10-11]: Padding
                # [12-15]: Param1 (FormID for GetIsID)
                # [16-19]: Param2
                # [20-23]: Run On type
                # [24-27]: Reference (if Run On = Target)
                # [28-31]: Unknown

                function_index = int.from_bytes(subrecord.data[8:10], byteorder="little")

                # GetIsID function - param1 is NPC FormID
                if function_index == GETISID_FUNCTION and speaker_formid is None:
                    param1_int = int.from_bytes(subrecord.data[12:16], byteorder="little")
                    if param1_int != 0:
                        speaker_formid = hex(param1_int).removeprefix("0x").upper().zfill(8)

            # TRDT - Response data containing emotion type
            elif isinstance(subrecord, TRDT):
                emotion_type = subrecord.emotion_type

            # NAM2 - Response note (if it's a StringSubrecord)
            elif subrecord.type == "NAM2" and isinstance(subrecord, StringSubrecord):
                if isinstance(subrecord.string, RawString):
                    response_note = str(subrecord.string)

        return speaker_formid, emotion_type, response_note

    @staticmethod
    def _extract_dial_context(record: Record) -> tuple[str | None, str | None, str | None]:
        """
        Extracts DIAL dialogue topic context (topic name, quest FormID, editor ID).

        Args:
            record (Record): The DIAL record to extract context from.

        Returns:
            tuple: (dialogue_topic, quest_formid, editor_id) - values or None if not found.
        """
        dialogue_topic: str | None = None
        quest_formid: str | None = None
        editor_id: str | None = None

        # Get EDID first
        edid = SSEPlugin.get_record_edid(record)
        if edid:
            editor_id = str(edid)

        for subrecord in record.subrecords:
            # FULL - Topic name
            if subrecord.type == "FULL" and isinstance(subrecord, StringSubrecord):
                if isinstance(subrecord.string, RawString):
                    dialogue_topic = str(subrecord.string)

            # QNAM - Quest FormID reference
            elif subrecord.type == "QNAM" and len(subrecord.data) >= 4:
                # Read FormID as little-endian uint32
                quest_formid_int = int.from_bytes(subrecord.data[:4], byteorder="little")
                quest_formid = hex(quest_formid_int).removeprefix("0x").upper().zfill(8)

        return dialogue_topic, quest_formid, editor_id

    # Extraction methods

    def extract_group_strings_with_context(
        self,
        group: Group,
        extract_localized: bool = False,
        dial_context: tuple[str | None, str | None] | None = None,
        dial_context_map: dict[str, tuple[str | None, str | None]] | None = None,
    ) -> dict[PluginStringWithContext, StringSubrecord]:
        """
        Extracts all strings from a group of records with full context.

        Args:
            group (Group): The group to extract strings from.
            extract_localized (bool, optional):
                Whether to extract localized strings. Defaults to False.
            dial_context (tuple, optional):
                Parent DIAL context (dialogue_topic, quest_formid) for INFO records.
            dial_context_map (dict, optional):
                Map of DIAL FormID to context, built for Normal DIAL groups.

        Returns:
            dict[PluginStringWithContext, StringSubrecord]:
                A dictionary mapping extracted strings to their subrecords.
        """
        strings: dict[PluginStringWithContext, StringSubrecord] = {}

        # For Normal DIAL groups, build a map of DIAL FormID -> context
        if (
            hasattr(group, "label")
            and group.label == "DIAL"
            and hasattr(group, "group_type")
            and group.group_type == Group.GroupType.Normal
        ):
            dial_context_map = {}
            for child in group.children:
                if isinstance(child, Record) and child.type == "DIAL":
                    # Build context for this DIAL record
                    context = self._extract_dial_context(child)
                    # Key is the FormID (matches TopicChildren group label)
                    dial_context_map[child.formid] = context

        # For TopicChildren groups, look up DIAL context using the group's label (DIAL FormID)
        dial_formid_for_info: str | None = None
        if hasattr(group, "group_type") and group.group_type == Group.GroupType.TopicChildren:
            if dial_context_map and hasattr(group, "label"):
                # The label of TopicChildren group is the DIAL FormID
                dial_formid_for_info = group.label
                if dial_formid_for_info in dial_context_map:
                    dial_context = dial_context_map[dial_formid_for_info]

        record: Record
        for record in SSEPlugin.extract_group_records(group, recursive=False):
            edid: RawString | None = SSEPlugin.get_record_edid(record)
            master_index = int(record.formid[:2], base=16)

            # Get plugin that first defines this record from masters
            master: str
            try:
                master = str(self._SSEPlugin__masters[master_index])
            except IndexError:
                master = self._SSEPlugin__plugin_name

            formid: str = f"{record.formid}|{master}"

            # Extract context based on record type
            npc_sex, npc_race, npc_class = None, None, None
            dialogue_topic, quest_formid = None, None
            speaker_formid, emotion_type, response_note = None, None, None

            if record.type == "NPC_":
                npc_sex, npc_race, npc_class = self._extract_npc_context(record)
            elif record.type == "INFO":
                # Always extract INFO-specific context
                speaker_formid, emotion_type, response_note = self._extract_info_context(record)
                # Apply DIAL context if available
                if dial_context:
                    dialogue_topic, quest_formid = dial_context[0], dial_context[1]

            for subrecord in record.subrecords:
                if isinstance(subrecord, StringSubrecord):
                    string: RawString | int = subrecord.string

                    if isinstance(string, RawString) or extract_localized:
                        string_data = PluginStringWithContext(
                            editor_id=str(edid) if edid else None,
                            form_id=formid,
                            index=subrecord.index,
                            type=f"{record.type} {subrecord.type}",
                            string=str(string),
                            npc_sex=npc_sex,
                            npc_race=npc_race,
                            npc_class=npc_class,
                            dialogue_topic=dialogue_topic,
                            quest_formid=quest_formid,
                            speaker_formid=speaker_formid,
                            emotion_type=emotion_type,
                            response_note=response_note,
                            dial_formid=dial_formid_for_info if record.type == "INFO" else None,
                        )

                        strings[string_data] = subrecord

        # Recursively process child groups with context propagation
        for child in group.children:
            if isinstance(child, Group):
                child_strings = self.extract_group_strings_with_context(
                    child, extract_localized, dial_context, dial_context_map
                )
                strings.update(child_strings)

        return strings

    def extract_strings_with_context(self, extract_localized: bool = False) -> list[PluginStringWithContext]:
        """
        Extracts all strings from the plugin with NPC context.

        Args:
            extract_localized (bool, optional):
                Whether to extract localized strings. Defaults to False.

        Returns:
            list[PluginStringWithContext]: A list of extracted strings with context.
        """
        strings: list[PluginStringWithContext] = []
        for group in self._SSEPlugin__groups:
            current_group: list[PluginStringWithContext] = list(
                self.extract_group_strings_with_context(group, extract_localized).keys()
            )
            strings.extend(current_group)

        return strings

    def find_string_subrecord(self, form_id: str, type: str, string: str, index: int | None) -> StringSubrecord | None:
        """
        Finds a subrecord that matches the given parameters.

        Args:
            form_id (str): Form ID of the subrecord.
            type (str): Type of the subrecord.
            string (str): String of the subrecord.
            index (Optional[int]): Index of the subrecord.

        Returns:
            Optional[StringSubrecord]: The found subrecord, or None if not found.
        """
        string_subrecord: StringSubrecord | None = None

        if self.__string_subrecords is None:
            string_subrecords: dict[PluginStringWithContext, StringSubrecord] = {}

            for group in self._SSEPlugin__groups:
                current_group = self.extract_group_strings_with_context(group)
                string_subrecords |= current_group

            self.__string_subrecords = string_subrecords

        for plugin_string, subrecord in self.__string_subrecords.items():
            if (
                plugin_string.form_id[2:] == form_id[2:]  # Ignore master index
                and plugin_string.type == type
                and plugin_string.string == string
                and plugin_string.index == index
            ):
                string_subrecord = subrecord
                break

        return string_subrecord
