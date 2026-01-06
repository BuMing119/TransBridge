from collections.abc import Callable
import logging
from pathlib import Path

from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.plugin_string import PluginString
from src.transbridge.converter.translation_entry import TranslationEntry


class PluginParser:
    """
    Bridges SSEPlugin and TranslationEntry model.
    Converts raw plugin strings into structured translation entries.
    """

    def __init__(self):
        self._plugin: SSEPlugin | None = None
        self._source_path: Path | None = None
        self.log = logging.getLogger("PluginParser")

    def parse_plugin(
        self,
        path: Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        skip_empty: bool = True,
    ) -> list[TranslationEntry]:
        """
        Parse a plugin file and return all translatable strings as TranslationItem objects.

        Args:
            path: Path to .esp/.esm file.
            progress_callback: Optional callback(current, total, description) for progress updates.
            skip_empty: If True, skip strings with empty original text (default: True).

        Returns:
            List of TranslationEntry objects.
        """
        self._source_path = path
        self.log.info(f"Starting to parse plugin: {path}")

        try:
            self._plugin = SSEPlugin.from_file(path)
        except Exception as e:
            self.log.error(f"Failed to parse plugin {path}: {e}")
            return []

        #strings_with_context = self._plugin.extract_strings_with_context()
        strings_with_context = self._plugin.extract_strings()
        total = len(strings_with_context)
        self.log.info(f"Extracted {total} strings from plugin")

        items = []
        skipped_count = 0

        for idx, ps in enumerate(strings_with_context):
            # Progress callback
            if progress_callback:
                progress_callback(idx + 1, total, f"{ps.editor_id}_{ps.type}")

            # Skip empty strings if requested
            if skip_empty and not ps.string.strip():
                skipped_count += 1
                self.log.debug(f"Skipped empty string: {ps.editor_id} {ps.type}")
                continue

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
        return TranslationEntry.creat_from_plugin_entry(ps)

    def get_plugin(self) -> SSEPlugin | None:
        """Get the underlying plugin object."""
        return self._plugin

    def get_source_path(self) -> Path | None:
        """Get the source file path."""
        return self._source_path
