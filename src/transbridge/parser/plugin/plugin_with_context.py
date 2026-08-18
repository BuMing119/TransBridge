"""
SSEPluginWithContext - Extended plugin parser with NPC context extraction.

This module extends the sse_plugin_interface to extract strings with additional
context information such as NPC sex, race, and class.
"""

import logging

from .item import (
    BaseContext,
    DialContext,
    InfoContext,
    NPCContext,
)
from transbridge.parser.utils.fromid_trans import formid_bytes_to_complete, formid_bytes_to_hex
from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.group import Group
from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import TRDT, StringSubrecord

from .plugin_string_with_context import PluginStringWithContext
from transbridge.parser.strings_file import PluginStringsLookup


class SSEPluginWithContext(SSEPlugin):
    """
    Extended SSE Plugin parser that extracts strings with NPC context.
    Based on sse_plugin_interface.SSEPlugin with additional context extraction.
    """

    log: logging.Logger = logging.getLogger("SSEPluginWithContext")

    def __init__(self, name: str) -> None:
        """
        Args:
            name (str): The name of the plugin.
        """
        super().__init__(name)
        self._string_subrecords: dict[PluginStringWithContext, StringSubrecord] | None = None

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

    # Helper methods

    EMOTION_MAP = {
        0: "Neutral",
        1: "Anger",
        2: "Disgust",
        3: "Fear",
        4: "Sad",
        5: "Happy",
        6: "Surprise",
        7: "Puzzled",
    }

    @staticmethod
    def _emotion_int_to_str(emotion_type: int | None) -> str | None:
        """Convert emotion type integer to readable string."""
        if emotion_type is None:
            return None
        return SSEPluginWithContext.EMOTION_MAP.get(emotion_type, f"Unknown({emotion_type})")

    # NPC Context extraction methods

    def _extract_npc_context(
        self,
        record: Record,
    ) -> NPCContext:
        """
        Extracts NPC context (sex, race, class) from an NPC_ record.

        Args:
            record (Record): The NPC_ record to extract context from.

        Returns:
            NPCContext: The extracted NPC context with complete FormIDs.
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
                npc_race = formid_bytes_to_complete(
                    subrecord.data,
                    [str(m) for m in self._SSEPlugin__masters],
                    self._SSEPlugin__plugin_name,
                )

            # CNAM - Class reference (FormID)
            elif subrecord.type == "CNAM" and len(subrecord.data) >= 4:
                npc_class = formid_bytes_to_complete(
                    subrecord.data,
                    [str(m) for m in self._SSEPlugin__masters],
                    self._SSEPlugin__plugin_name,
                )

        return NPCContext(npc_sex=npc_sex, npc_race=npc_race, npc_class=npc_class)

    def _extract_info_context(
        self,
        record: Record,
        dial_context: DialContext | None = None,
        parent_dial_id: str | None = None,
    ) -> InfoContext:
        """
        Extracts INFO dialogue context.

        Args:
            record (Record): The INFO record to extract context from.
            dial_context (DialContext, optional): Parent DIAL context.
            parent_dial_id (str, optional): Parent DIAL FormID (hex only, not complete).

        Returns:
            InfoContext: The extracted INFO context with complete FormIDs.
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
                speaker_formid = formid_bytes_to_complete(
                    subrecord.data,
                    [str(m) for m in self._SSEPlugin__masters],
                    self._SSEPlugin__plugin_name,
                )

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
                        speaker_formid = formid_bytes_to_complete(
                            subrecord.data[12:16],
                            [str(m) for m in self._SSEPlugin__masters],
                            self._SSEPlugin__plugin_name,
                        )

            # TRDT - Response data containing emotion type
            elif isinstance(subrecord, TRDT):
                emotion_type = subrecord.emotion_type

            # NAM2 - Response note (if it's a StringSubrecord)
            elif subrecord.type == "NAM2" and isinstance(subrecord, StringSubrecord):
                if isinstance(subrecord.string, RawString):
                    response_note = str(subrecord.string)

        return InfoContext(
            quest=dial_context.quest if dial_context else None,
            dialogue_topic=parent_dial_id,
            speaker=speaker_formid,
            emotion=SSEPluginWithContext._emotion_int_to_str(emotion_type),
            response_note=response_note,
        )

    def _extract_dial_context(self, record: Record, dlbr_map: dict[str, tuple[str, str]] | None = None) -> DialContext:
        """
        Extracts DIAL dialogue topic context.

        Args:
            record (Record): The DIAL record to extract context from.
            dlbr_map (dict, optional): Map of DIAL hex ID -> (Quest complete FormID, DLBR complete FormID).

        Returns:
            DialContext: The extracted DIAL context with complete FormIDs.
        """

        quest_formid: str | None = None

        # Check DLBR map for Quest ID first (most reliable for DIAL)
        if dlbr_map and record.formid in dlbr_map:
            quest_formid, dialogue_branch = dlbr_map[record.formid]
        else:
            dialogue_branch = None

        for subrecord in record.subrecords:
            # QNAM - Quest FormID reference (fallback if present)
            if subrecord.type == "QNAM" and len(subrecord.data) >= 4 and quest_formid is None:
                quest_formid = formid_bytes_to_complete(
                    subrecord.data,
                    [str(m) for m in self._SSEPlugin__masters],
                    self._SSEPlugin__plugin_name,
                )

        return DialContext(
            quest=quest_formid,
            dialogue_branch=dialogue_branch,
        )

    def _build_dlbr_map(self) -> dict[str, tuple[str, str]]:
        """
        Scans all groups for DLBR records to build a DIAL->(Quest, DLBR) map.

        Returns:
            dict[str, tuple[str, str]]: Map of {DIAL_FormID_hex: (Quest_complete_FormID, DLBR_complete_FormID)}
        """
        dlbr_map: dict[str, tuple[str, str]] = {}

        # Iterate through top-level groups to find DLBR group
        for group in self._SSEPlugin__groups:
            if hasattr(group, "label") and group.label == "DLBR":
                # Found DLBR group, iterate its records
                for record in SSEPlugin.extract_group_records(group):
                    if record.type == "DLBR":
                        quest_id: str | None = None
                        dial_id_hex: str | None = None

                        for sub in record.subrecords:
                            if sub.type == "QNAM" and len(sub.data) >= 4:
                                quest_id = formid_bytes_to_complete(
                                    sub.data,
                                    [str(m) for m in self._SSEPlugin__masters],
                                    self._SSEPlugin__plugin_name,
                                )
                            elif sub.type == "SNAM" and len(sub.data) >= 4:
                                # DIAL ID as hex only (used as key for lookup)
                                dial_id_hex = formid_bytes_to_hex(sub.data)

                        if quest_id and dial_id_hex:
                            # Generate complete FormID for DLBR record
                            master_index = int(record.formid[:2], base=16)
                            try:
                                master = str(self._SSEPlugin__masters[master_index])
                            except IndexError:
                                master = self._SSEPlugin__plugin_name
                            dlbr_formid = f"{record.formid}|{master}"

                            dlbr_map[dial_id_hex] = (quest_id, dlbr_formid)

        self.log.debug(f"Built DLBR map with {len(dlbr_map)} entries")
        return dlbr_map

    # Extraction methods

    def extract_group_strings_with_context(
        self,
        group: Group,
        extract_localized: bool = False,
        dial_context: DialContext | None = None,
        dial_context_map: dict[str, DialContext] | None = None,
        dlbr_map: dict[str, tuple[str, str]] | None = None,
        strings_lookup: PluginStringsLookup | None = None,
    ) -> dict[PluginStringWithContext, StringSubrecord]:
        """
        Extracts all strings from a group of records with full context.

        Args:
            group (Group): The group to extract strings from.
            extract_localized (bool, optional):
                Whether to extract localized strings. Defaults to False.
            dial_context (DialContext, optional):
                Parent DIAL context for INFO records.
            dial_context_map (dict, optional):
                Map of DIAL FormID to context, built for Normal DIAL groups.
            dlbr_map (dict, optional):
                Map of DIAL FormID to (Quest FormID, DLBR FormID).

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
                    context = self._extract_dial_context(child, dlbr_map)
                    # Key is the FormID (matches TopicChildren group label)
                    dial_context_map[child.formid] = context

        # For TopicChildren groups, look up DIAL context using the group's label (DIAL FormID)
        dial_formid_for_info: str | None = None
        if hasattr(group, "group_type") and group.group_type == Group.GroupType.TopicChildren:
            if hasattr(group, "label"):
                # The label of TopicChildren group is the DIAL FormID (can be str or bytes)
                label_raw = group.label
                label_hex: str | None = None

                if isinstance(label_raw, bytes):
                    label_hex = formid_bytes_to_hex(label_raw)
                    dial_formid_for_info = formid_bytes_to_complete(
                        label_raw,
                        [str(m) for m in self._SSEPlugin__masters],
                        self._SSEPlugin__plugin_name,
                    )
                elif isinstance(label_raw, str):
                    # Already hex string
                    label_hex = label_raw

                    # Convert to complete FormID
                    master_index = int(label_hex[:2], base=16)
                    try:
                        master = str(self._SSEPlugin__masters[master_index])
                    except IndexError:
                        master = self._SSEPlugin__plugin_name

                    dial_formid_for_info = f"{label_hex}|{master}"

                if label_hex and dial_context_map and label_hex in dial_context_map:
                    dial_context = dial_context_map[label_hex]

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
            context_data: BaseContext | None = None

            if record.type == "NPC_":
                context_data = self._extract_npc_context(record)
            elif record.type == "INFO":
                context_data = self._extract_info_context(record, dial_context, dial_formid_for_info)
            elif record.type == "DIAL":
                context_data = self._extract_dial_context(record, dlbr_map)

            for subrecord in record.subrecords:
                if isinstance(subrecord, StringSubrecord):
                    string: RawString | int = subrecord.string

                    # 提取 string_id（本地化插件为 int，否则为 None）
                    raw_string_id: int | None = None

                    if isinstance(string, RawString):
                        string_text = str(string)
                    elif strings_lookup is not None:
                        # Localized plugin: string is an integer index into external strings files
                        raw_string_id = string  # 保存 int ID
                        resolved = strings_lookup.get(string)
                        if resolved is None:
                            self.log.debug(
                                f"Unresolved string ID {string:#010x} "
                                f"in {record.type} {subrecord.type} ({formid})"
                            )
                            continue
                        string_text = resolved
                    elif extract_localized:
                        # No lookup available but caller wants raw indices
                        string_text = str(string)
                    else:
                        continue

                    string_data = PluginStringWithContext(
                        editor_id=str(edid) if edid else None,
                        form_id=formid,
                        index=subrecord.index,
                        type=f"{record.type} {subrecord.type}",
                        string=string_text,
                        context=context_data,
                        string_id=raw_string_id,  # 传递 string_id
                    )

                    strings[string_data] = subrecord

        # Recursively process child groups with context propagation
        for child in group.children:
            if isinstance(child, Group):
                child_strings = self.extract_group_strings_with_context(
                    child, extract_localized, dial_context, dial_context_map, dlbr_map,
                    strings_lookup,
                )
                strings.update(child_strings)

        return strings

    def extract_strings_with_context(
        self,
        extract_localized: bool = False,
        strings_lookup: PluginStringsLookup | None = None,
    ) -> list[PluginStringWithContext]:
        """
        Extracts all strings from the plugin with NPC context.

        Args:
            extract_localized (bool, optional):
                Whether to extract localized strings. Defaults to False.
            strings_lookup (PluginStringsLookup, optional):
                Lookup table for resolving localized string indices to actual
                text. When provided, localised strings (stored as UInt32
                indices) are resolved and included automatically regardless of
                *extract_localized*.

        Returns:
            list[PluginStringWithContext]: A list of extracted strings with context.
        """
        strings: list[PluginStringWithContext] = []
        dlbr_map = self._build_dlbr_map()

        for group in self._SSEPlugin__groups:
            current_group: list[PluginStringWithContext] = list(
                self.extract_group_strings_with_context(
                    group, extract_localized, dlbr_map=dlbr_map,
                    strings_lookup=strings_lookup,
                ).keys()
            )
            strings.extend(current_group)

        # 按 form_id 排序确保顺序稳定可复现
        strings.sort(key=lambda x: x.form_id or "")
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

        if self._string_subrecords is None:
            string_subrecords: dict[PluginStringWithContext, StringSubrecord] = {}

            for group in self._SSEPlugin__groups:
                current_group = self.extract_group_strings_with_context(group)
                string_subrecords |= current_group

            self._string_subrecords = string_subrecords

        for plugin_string, subrecord in self._string_subrecords.items():
            if (
                plugin_string.form_id[2:] == form_id[2:]  # Ignore master index
                and plugin_string.type == type
                and plugin_string.string == string
                and plugin_string.index == index
            ):
                string_subrecord = subrecord
                break

        return string_subrecord