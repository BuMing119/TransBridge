from collections.abc import Callable
import logging
from pathlib import Path

from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.plugin_string import PluginString
from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from src.transbridge.parser.strings_file import PluginStringsLookup


class PluginParser:
    """
    Bridges SSEPluginWithContext and TranslationEntry model.
    Converts raw plugin strings into structured translation entries.
    """

    def __init__(self):
        self._plugin: SSEPluginWithContext | None = None
        self._source_path: Path | None = None
        self._strings_lookup: PluginStringsLookup | None = None
        self.log = logging.getLogger("PluginParser")

    def parse_plugin(
        self,
        path: Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        skip_empty: bool = True,
        language: str = "english",
    ) -> list[TranslationEntry]:
        """
        Parse a plugin file and return all translatable strings as TranslationItem objects.

        Args:
            path: Path to .esp/.esm file.
            progress_callback: Optional callback(current, total, description) for progress updates.
            skip_empty: If True, skip strings with empty original text (default: True).
            language: Language to look up for localized plugins (default: "english").

        Returns:
            List of TranslationEntry objects.
        """
        self._source_path = path
        self.log.info(f"Starting to parse plugin: {path}")

        try:
            self._plugin = SSEPluginWithContext.from_file(path)
        except Exception as e:
            self.log.error(f"Failed to parse plugin {path}: {e}")
            return []

        # Try to load external strings files for localised plugins (e.g. Skyrim.esm)
        strings_lookup = PluginStringsLookup.from_plugin(path, language=language)
        self._strings_lookup = strings_lookup
        if strings_lookup:
            self.log.info(f"Loaded strings lookup with {len(strings_lookup)} entries for {path.name}")

        strings_with_context = self._plugin.extract_strings_with_context(strings_lookup=strings_lookup)
        total = len(strings_with_context)
        self.log.info(f"Extracted {total} strings from plugin")

        # 第一遍：建立 DIAL_form_id -> DIAL_editor_id 的映射
        # 同一个 DIAL 下的 INFO 用该 DIAL 的 editor_id 填充
        dial_formid_to_edid: dict[str, str] = {}
        for ps in strings_with_context:
            if ps.type and ps.type.startswith("DIAL ") and ps.editor_id:
                # ps.form_id 格式为 "hex|plugin"，取 hex 部分作为 key
                dial_hex = ps.form_id.split("|")[0]
                dial_formid_to_edid[dial_hex] = ps.editor_id
                self.log.debug(f"Mapped DIAL {dial_hex} -> editor_id {ps.editor_id}")

        self.log.info(f"Built DIAL form_id->editor_id map with {len(dial_formid_to_edid)} entries")

        items = []
        skipped_count = 0

        for idx, ps in enumerate(strings_with_context):
            # Progress callback
            editor_id_display = ps.editor_id or "<None>"
            if progress_callback:
                progress_callback(idx + 1, total, f"{editor_id_display}_{ps.type}")

            # Skip empty strings if requested
            if skip_empty and not ps.string.strip():
                skipped_count += 1
                self.log.debug(f"Skipped empty string: {ps.editor_id} {ps.type}")
                continue

            # EET 风格的 editor_id 填充：
            # 对于 INFO 记录，如果没有 editor_id，用其所属 DIAL 的 editor_id 填充
            if ps.editor_id is None and ps.type and ps.type.startswith("INFO "):
                if ps.context and hasattr(ps.context, "dialogue_topic") and ps.context.dialogue_topic:
                    # dialogue_topic 格式为 "hex|plugin"，取 hex 部分
                    dial_hex = ps.context.dialogue_topic.split("|")[0]
                    if dial_hex in dial_formid_to_edid:
                        dial_edid = dial_formid_to_edid[dial_hex]
                        setattr(ps, "editor_id", dial_edid)
                        self.log.debug(f"Filled INFO editor_id with DIAL '{dial_edid}'")

            item = self._create_item(ps)
            items.append(item)

        if skipped_count > 0:
            self.log.info(f"Skipped {skipped_count} empty strings ({skipped_count / total * 100:.1f}%)")
        self.log.info(f"Successfully parsed {len(items)} translation items")
        return items

    def _create_item(self, ps: PluginString) -> TranslationEntry:
        """Convert PluginStringWithContext to TranslationEntry."""
        # Replace space with colon in type, e.g. "INFO NAM1" -> "INFO:NAM1"
        # key = ps.type.replace(" ", ":") if ps.type else "UNKNOWN"
        #
        # return TranslationEntry(
        #     #id=ps.form_id,
        #     id=f"{ps.editor_id}:{ps.form_id}",
        #     key=key,
        #     original=ps.string,
        #     translation="",
        #     stage=0,
        #     context=None,
        # )
        return TranslationEntry.create_from_plugin_entry(ps)

    def get_plugin(self) -> SSEPlugin | None:
        """Get the underlying plugin object."""
        return self._plugin

    def get_strings_lookup(self) -> PluginStringsLookup | None:
        """
        Return the strings lookup loaded during the last parse_plugin() call.

        Returns None if the plugin is not localised or parse_plugin() has not
        been called yet.
        """
        return self._strings_lookup

    def get_source_path(self) -> Path | None:
        """Get the source file path."""
        return self._source_path
