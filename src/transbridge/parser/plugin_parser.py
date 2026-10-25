from collections.abc import Callable
import logging
from pathlib import Path

from sse_plugin_interface.flags import RecordFlags
from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.plugin_string import PluginString

from transbridge.application.contracts import Diagnostic
from transbridge.converter.plugin_entry_conflicts import resolve_plugin_entry_conflicts
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.strings_file import PluginStringsLookup


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
        self.conflict_diagnostics: tuple[Diagnostic, ...] = ()
        self.conflict_skipped_count = 0

    def parse_plugin(
        self,
        path: Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        skip_empty: bool = True,
        language: str = "english",
        discover_sibling_strings: bool = True,
        raise_on_error: bool = False,
    ) -> list[TranslationEntry]:
        """
        Parse a plugin file and return all translatable strings as TranslationItem objects.

        Args:
            path: Path to .esp/.esm file.
            progress_callback: Optional callback(current, total, description) for progress updates.
            skip_empty: If True, skip strings with empty original text (default: True).
            language: Language to look up for localized plugins (default: "english").
            discover_sibling_strings: Whether to inspect the plugin's sibling
                ``Strings`` directory (default: True for legacy compatibility).
            raise_on_error: Propagate file loading errors so interactive callers
                can distinguish failure from a valid plugin with no strings.
                Also require a complete TES4 header whose declared contents fit
                within the source file, before invoking the binary parser.

        Returns:
            List of TranslationEntry objects.
        """
        self._source_path = path
        self._plugin = None
        self._strings_lookup = None
        self.conflict_diagnostics = ()
        self.conflict_skipped_count = 0
        self.log.info("Starting to parse plugin: %s", path)

        try:
            if raise_on_error:
                with path.open("rb") as stream:
                    header = stream.read(24)
                    file_size = stream.seek(0, 2)
                if len(header) < 24 or header[:4] != b"TES4":
                    raise ValueError(f"Plugin is missing a complete TES4 header: {path}")
                if int.from_bytes(header[4:8], "little") > file_size - 24:
                    raise ValueError(f"Plugin TES4 header data exceeds the source file: {path}")
            self._plugin = SSEPluginWithContext.from_file(path)
        except Exception as e:
            self.log.error("Failed to parse plugin %s: %s", path, e)
            if raise_on_error:
                raise
            return []

        # Try to load external strings files for localised plugins (e.g. Skyrim.esm)
        discover_archive = True
        if discover_sibling_strings:
            try:
                with path.open("rb") as source:
                    header = source.read(12)
            except FileNotFoundError:
                # Legacy callers may provide an already-loaded/mocked plugin.
                header = b""
            if len(header) == 12 and header[:4] == b"TES4":
                discover_archive = bool(int.from_bytes(header[8:12], "little") & RecordFlags.Localized)
        strings_lookup = None
        if discover_sibling_strings:
            strings_lookup = (
                PluginStringsLookup.from_plugin(path, language=language)
                if discover_archive
                else PluginStringsLookup.from_strings_dir(path.parent / "Strings", path.stem, language)
            )
        self._strings_lookup = strings_lookup
        if strings_lookup:
            self.log.info("Loaded strings lookup with %d entries for %s", len(strings_lookup), path.name)

        strings_with_context = self._plugin.extract_strings_with_context(strings_lookup=strings_lookup)
        items = self.create_entries(strings_with_context, progress_callback=progress_callback, skip_empty=False)
        items, self.conflict_diagnostics = resolve_plugin_entry_conflicts(items)
        self.conflict_skipped_count = len(self.conflict_diagnostics)
        if self.conflict_skipped_count:
            self.log.warning(
                "SOURCE_LOCATOR_CONFLICT: 插件 %s 中 %d 条定位与原文均相同的条目已跳过，保留 %d 条。",
                path,
                self.conflict_skipped_count,
                len(items),
            )
        if skip_empty:
            items = [entry for entry in items if entry.original.strip()]
        return items

    def create_entries(self, strings_with_context, *, progress_callback=None, skip_empty=True):
        """Apply the same source identity conventions to parsing and physical writes."""
        total = len(strings_with_context)
        self.log.info("Extracted %d strings from plugin", total)

        # 第一遍：建立 DIAL_form_id -> DIAL_editor_id 的映射
        # 同一个 DIAL 下的 INFO 用该 DIAL 的 editor_id 填充
        dial_formid_to_edid: dict[str, str] = {}
        for ps in strings_with_context:
            if ps.type and ps.type.startswith("DIAL ") and ps.editor_id:
                # ps.form_id 格式为 "hex|plugin"，取 hex 部分作为 key
                dial_hex = ps.form_id.split("|")[0]
                dial_formid_to_edid[dial_hex] = ps.editor_id

        self.log.info("Built DIAL form_id->editor_id map with %d entries", len(dial_formid_to_edid))

        items = []
        skipped_count = 0
        filled_info_count = 0

        for idx, ps in enumerate(strings_with_context):
            # Progress callback
            editor_id_display = ps.editor_id or "<None>"
            if progress_callback:
                progress_callback(idx + 1, total, f"{editor_id_display}_{ps.type}")

            # Skip empty strings if requested
            if skip_empty and not ps.string.strip():
                skipped_count += 1
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
                        filled_info_count += 1

            item = self._create_item(ps, source_order=idx)
            items.append(item)

        if skipped_count > 0:
            self.log.info("Skipped %d empty strings (%.1f%%)", skipped_count, skipped_count / total * 100)
        if filled_info_count:
            self.log.debug("Filled INFO editor_id from DIAL for %d entries", filled_info_count)
        self.log.info("Successfully parsed %d translation items", len(items))
        return items

    def _create_item(self, ps: PluginString, *, source_order: int | None = None) -> TranslationEntry:
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
        return TranslationEntry.create_from_plugin_entry(ps, source_order=source_order)

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
