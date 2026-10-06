from collections import defaultdict
import logging
from pathlib import Path

from sse_plugin_interface.datatypes import RawString

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.strings_file import PluginStringsLookup, PluginStringsWriter

from .plugin_source import capture_plugin_write_source

log = logging.getLogger("PluginWriter")


class PluginWriter:
    """
    将 TranslationEntryCollection 的内容反向写入 SSEPlugin 文件。

    支持两种模式：
    - 非本地化插件：直接将译文写入记录中的字符串子记录（inline）。
    - 本地化插件（需传入 strings_lookup）：收集 string_id → 译文，
      在 write() 时同步输出 .strings/.dlstrings/.ilstrings 文件。
    """

    def __init__(
        self,
        plugin: SSEPluginWithContext,
        strings_lookup: PluginStringsLookup | None = None,
        language: str = "english",
    ) -> None:
        """
        Args:
            plugin: 已读取的插件实例。
            strings_lookup: 本地化插件的字符串查表，None 表示非本地化模式。
            language: 输出 strings 文件使用的语言标签（默认 "english"）。
        """
        self.plugin = plugin
        self.strings_lookup = strings_lookup
        self._language = language
        self._inline_count = 0  # 本地化模式下 inline 修改的数量
        self._localized_writer: PluginStringsWriter | None = (
            PluginStringsWriter() if strings_lookup is not None else None
        )

    def apply_collection(self, collection: TranslationEntryCollection) -> int:
        """
        根据 TranslationEntryCollection 更新 plugin 字符串。

        :return: 实际更新的字符串数
        """
        source_pairs = capture_plugin_write_source(self.plugin, self.strings_lookup)
        source_groups = defaultdict(list)
        requested_groups = defaultdict(list)
        for source, subrecord in source_pairs:
            source_groups[source.key].append((source, subrecord))
        for entry in collection:
            requested_groups[entry.key].append(entry)

        updated_count = 0
        self._inline_count = 0
        for key, candidates in source_groups.items():
            requests = requested_groups.get(key, ())
            if not requests:
                continue
            original_counts = defaultdict(int)
            for source, _ in candidates:
                original_counts[source.original] += 1
            for source, subrecord in candidates:
                if original_counts[source.original] != 1:
                    log.warning("SOURCE_LOCATOR_CONFLICT: skipped indistinguishable plugin locator %s", key)
                    continue
                matches = [
                    entry
                    for entry in requests
                    if (len(candidates) == 1 or entry.requires_original_match)
                    and (not entry.requires_original_match or entry.original == source.original)
                ]
                if len(matches) != 1:
                    continue
                entry = matches[0]
                text = entry.translation or source.original
                if isinstance(subrecord.string, int):
                    if self._localized_writer is None:
                        continue
                    self._localized_writer.add(subrecord.string, text, source.dsd_type.split()[-1])
                elif isinstance(subrecord.string, RawString):
                    if text == str(subrecord.string):
                        continue
                    subrecord.set_string(text)
                    self._inline_count += 1
                else:
                    continue
                updated_count += 1
        log.info("apply_collection: updated=%d, inline=%d", updated_count, self._inline_count)
        return updated_count

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self, output_path: str | Path) -> dict:
        """
        将修改后的插件保存到文件。
        本地化模式下同时输出 Strings/ 子文件夹中的 strings 文件。

        Returns:
            dict: 包含写入统计信息
                - esp_saved: bool, 是否保存了 ESP 文件
                - strings_written: list[Path], 已写入的 strings 文件路径列表
        """
        output_path = Path(output_path)
        result = {"esp_saved": False, "strings_written": []}

        # 非本地化模式：保存 ESP 文件
        # 本地化模式：仅当有 inline 字符串被修改时才保存 ESP
        need_save_esp = self.strings_lookup is None or self._inline_count > 0
        if need_save_esp:
            self.plugin.save(output_path)
            result["esp_saved"] = True

        if self._localized_writer:
            written = self._localized_writer.write(
                output_path.parent,
                output_path.stem,
                self._language,
            )
            result["strings_written"] = written
            if not written:
                log.warning("Localised mode active but no strings were written (empty collection?)")

        return result
