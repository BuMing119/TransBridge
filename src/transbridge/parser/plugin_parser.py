from collections.abc import Callable
import hashlib
import logging
from pathlib import Path

from src.transbridge.parser.plugin.item import (
    GenericContext,
    InfoContext,
    TranslationItem,
    TranslationMetadata,
)
from src.transbridge.parser.plugin.plugin_string_with_context import (
    PluginStringWithContext,
)
from src.transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from src.transbridge.parser.utils.text_cleaning import clean_string


class PluginParser:
    """
    Bridges SSEPluginWithContext and TranslationItem model.
    Converts raw plugin strings into structured translation items.
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
    ) -> list[TranslationItem]:
        """
        Parse a plugin file and return all translatable strings as TranslationItem objects.

        Args:
            path: Path to .esp/.esm file.
            progress_callback: Optional callback(current, total, description) for progress updates.
            skip_empty: If True, skip strings with empty original text (default: True).

        Returns:
            List of TranslationItem objects ready for DB storage.
        """
        self._source_path = path
        self.log.info(f"Starting to parse plugin: {path}")

        try:
            self._plugin = SSEPluginWithContext.from_file(path)
        except Exception as e:
            self.log.error(f"Failed to parse plugin {path}: {e}")
            return []

        strings_with_context = self._plugin.extract_strings_with_context()
        total = len(strings_with_context)
        self.log.info(f"Extracted {total} strings from plugin")

        items = []
        skipped_count = 0

        # Track DIAL-INFO relationships
        form_id_to_items: dict[str, list[TranslationItem]] = {}  # Store all items per form_id

        for idx, ps in enumerate(strings_with_context):
            # Progress callback
            if progress_callback:
                progress_callback(idx + 1, total, f"{ps.form_id}_{ps.type}")

            # Skip empty strings if requested
            if skip_empty and not ps.string.strip():
                skipped_count += 1
                self.log.debug(f"Skipped empty string: {ps.form_id} {ps.type}")
                continue

            item = self._create_item_from_context(ps)
            items.append(item)

            # Track all items with the same form_id
            if ps.form_id not in form_id_to_items:
                form_id_to_items[ps.form_id] = []
            form_id_to_items[ps.form_id].append(item)

            # If this is an INFO record with a parent DIAL, record the relationship
            if item.record_type == "INFO" and isinstance(ps.context, InfoContext) and ps.context.dialogue_topic:
                form_id_parts = ps.form_id.split("|")
                if len(form_id_parts) == 2:
                    plugin_name = form_id_parts[1]
                    dial_formid = f"{ps.context.dialogue_topic}|{plugin_name}"

                    if dial_formid in form_id_to_items:
                        for dial_item in form_id_to_items[dial_formid]:
                            if item.form_id not in dial_item.context.related_items:
                                dial_item.context.related_items.append(item.form_id)
                        self.log.debug(f"Linked INFO {item.form_id} to parent DIAL {dial_formid}")

                    # Add DIAL ID to INFO related_items
                    item.context.related_items.append(dial_formid)

        if skipped_count > 0:
            self.log.info(f"Skipped {skipped_count} empty strings ({skipped_count / total * 100:.1f}%)")
        self.log.info(f"Successfully parsed {len(items)} translation items")
        return items

    def _create_item_from_context(self, ps: PluginStringWithContext) -> TranslationItem:
        """Convert PluginStringWithContext to TranslationItem."""
        # Clean the original string
        cleaned_original = clean_string(ps.string)

        # Generate unique ID from form_id + field + index + source (using cleaned string)
        unique_str = f"{ps.form_id}_{ps.type}_{str(ps.index) + '_' if ps.index else ''}{cleaned_original}"
        item_id = hashlib.md5(unique_str.encode()).hexdigest()[:16]

        # Split type into record_type and field_name (e.g., "INFO NAM1" -> "INFO", "NAM1")
        parts = ps.type.split()
        record_type, field_name = (
            (parts[0], parts[1]) if len(parts) >= 2 else (parts[0] if parts else "UNKNOWN", "UNKNOWN")
        )
        if len(parts) < 2:
            self.log.warning(f"Unexpected type format: '{ps.type}' for {ps.form_id}")

        # Use the context from the plugin string directly
        context = ps.context if ps.context else GenericContext()

        # Build metadata with original_raw if cleaning changed the string
        metadata = TranslationMetadata(
            source=self._source_path.name if self._source_path else "unknown",
            original_hash=hashlib.md5(ps.string.encode()).hexdigest(),
            original_raw=ps.string if ps.string != cleaned_original else None,
        )

        return TranslationItem(
            id=item_id,
            form_id=ps.form_id,
            editor_id=ps.editor_id or "",
            index=ps.index,
            record_type=record_type,
            field_name=field_name,
            original=cleaned_original,  # Use cleaned version
            context=context,
            metadata=metadata,
        )

    def get_plugin(self) -> SSEPluginWithContext | None:
        """Get the underlying plugin object."""
        return self._plugin

    def get_source_path(self) -> Path | None:
        """Get the source file path."""
        return self._source_path
