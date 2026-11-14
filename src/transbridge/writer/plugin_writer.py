# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from collections import defaultdict
import logging
from pathlib import Path

from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.subrecord import StringSubrecord

from transbridge.config.text_encoding import DEFAULT_TEXT_ENCODING, normalize_text_encoding
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin.binary_io import PluginBinaryIO
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.plugin.text_subrecords import EncodedStringSubrecord
from transbridge.parser.strings_file import PluginStringsLookup

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
        *,
        output_encoding: str = DEFAULT_TEXT_ENCODING,
    ) -> None:
        """
        Args:
            plugin: 已读取的插件实例。
            strings_lookup: 本地化插件的字符串查表，None 表示非本地化模式。
            language: 输出 strings 文件使用的语言标签（默认 "english"）。
            output_encoding: 写入插件普通显示文本及 Strings 的编码。
        """
        self.plugin = plugin
        self.strings_lookup = strings_lookup
        self._language = language
        self._output_encoding = normalize_text_encoding(output_encoding)
        self._inline_count = 0  # 本地化模式下 inline 修改的数量
        self._localized_texts: dict[tuple[int, str], str] = {}
        self._applied = False

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
        inline_texts: dict[StringSubrecord, str] = {}
        localized_texts: dict[tuple[int, str], str] = {}
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
                    if self.strings_lookup is None:
                        continue
                    localized_texts[subrecord.string, source.dsd_type.split()[-1]] = text
                elif isinstance(subrecord.string, RawString):
                    if text == str(subrecord.string):
                        continue
                    inline_texts[subrecord] = text
                else:
                    continue
                updated_count += 1

        # Validate every physical inline field, even if the matching/editing
        # stage skipped it. A failed encode must leave the loaded plugin intact.
        encoded = []
        inline_fields = (
            self.plugin.inline_text_fields()
            if isinstance(self.plugin, PluginBinaryIO)
            else (field for _, field in source_pairs if isinstance(field, EncodedStringSubrecord))
        )
        for subrecord in inline_fields:
            text = inline_texts.pop(subrecord, str(subrecord.string))
            data = subrecord.encode_text(text, self._output_encoding)
            if data != subrecord.data or self._output_encoding != subrecord.string.encoding:
                encoded.append((subrecord, text, data))
        self._inline_count = sum(data != subrecord.data for subrecord, _text, data in encoded) + len(inline_texts)
        for subrecord, text, data in encoded:
            subrecord.set_encoded_text(text, self._output_encoding, data)
        for subrecord, text in inline_texts.items():
            subrecord.set_string(text)
        self._localized_texts.update(localized_texts)
        self._applied = True
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
        if not self._applied:
            self.apply_collection(TranslationEntryCollection())

        # 非本地化模式：保存 ESP 文件
        # 本地化模式：仅当有 inline 字符串被修改时才保存 ESP
        need_save_esp = self.strings_lookup is None or self._inline_count > 0
        if self.strings_lookup is not None:
            localized_writer = self.strings_lookup.create_writer()
            for (string_id, subrecord_type), text in self._localized_texts.items():
                localized_writer.add(string_id, text, subrecord_type)
            result["strings_written"] = localized_writer.write(
                output_path.parent,
                output_path.stem,
                self._language,
                output_encoding=self._output_encoding,
            )
        if need_save_esp:
            self.plugin.save(output_path)
            result["esp_saved"] = True

        return result
