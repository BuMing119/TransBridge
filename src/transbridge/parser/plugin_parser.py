from collections.abc import Callable
import logging
from pathlib import Path

from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.plugin_string import PluginString
from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext


class PluginParser:
    """
    Bridges SSEPluginWithContext and TranslationEntry model.
    Converts raw plugin strings into structured translation entries.
    """

    def __init__(self):
        self._plugin: SSEPluginWithContext | None = None
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
            self._plugin = SSEPluginWithContext.from_file(path)
        except Exception as e:
            self.log.error(f"Failed to parse plugin {path}: {e}")
            return []

        strings_with_context = self._plugin.extract_strings_with_context()
        #strings_with_context = self._plugin.extract_strings()
        total = len(strings_with_context)
        self.log.info(f"Extracted {total} strings from plugin")

        items = []
        skipped_count = 0
        last_editor_id = None  # 用于存储上一个有效的 editor_id

        for idx, ps in enumerate(strings_with_context):
            # Progress callback
            if progress_callback:
                progress_callback(idx + 1, total, f"{ps.editor_id}_{ps.type}")

            # Skip empty strings if requested
            if skip_empty and not ps.string.strip():
                skipped_count += 1
                self.log.debug(f"Skipped empty string: {ps.editor_id} {ps.type}")
                continue

            # 修复 editor_id 为 None 的问题
            # 如果当前 ps 的 editor_id 为 None，且 context 不为 "REFR:FULL"，则使用上一个有效的 editor_id
            if ps.editor_id is None and ps.type and ps.type.replace(" ", ":") != "REFR:FULL":
                # 创建一个新的 PluginString 对象，使用上一个有效的 editor_id
                # 由于 PluginString 可能是不可变的，我们使用 setattr 来修改它
                if last_editor_id is not None:
                    setattr(ps, "editor_id", last_editor_id)
                    self.log.debug(f"Fixed missing editor_id: set to {last_editor_id} for type {ps.type}")
            elif ps.editor_id is not None:
                # 如果当前 editor_id 有效，则更新 last_editor_id
                last_editor_id = ps.editor_id


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

    def get_source_path(self) -> Path | None:
        """Get the source file path."""
        return self._source_path
