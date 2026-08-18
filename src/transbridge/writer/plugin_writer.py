from pathlib import Path
import logging

from sse_plugin_interface.datatypes import RawString
from sse_plugin_interface.subrecord import StringSubrecord

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin.plugin_string_with_context import PluginStringWithContext
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.strings_file import PluginStringsLookup, PluginStringsWriter

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
        if self.strings_lookup is not None:
            return self._apply_localized_collection(collection)
        return self._apply_inline_collection(collection)

    # ------------------------------------------------------------------
    # Inline (non-localised) path — original logic
    # ------------------------------------------------------------------

    def _apply_inline_collection(self, collection: TranslationEntryCollection) -> int:
        updated_count = 0
        last_editor_id = None

        total_strings = 0
        collection_hits = 0
        subrecord_found = 0
        subrecord_not_found = 0


        #for ps in self.plugin.extract_strings():
        for ps in self.plugin.extract_strings_with_context():
            total_strings += 1

            editor_id = ps.editor_id
            if editor_id is None:
                if ps.type and ps.type.replace(" ", ":") != "REFR:FULL":
                    if last_editor_id is not None:
                        editor_id = last_editor_id
            else:
                last_editor_id = editor_id

            index = ps.index if ps.index is not None else 1
            key = ps.type.replace(" ", ":")
            form_id = str(ps.form_id).split("|")[0] if "|" in str(ps.form_id) else str(ps.form_id)
            entry_id = f"{editor_id}:{form_id}|{index}~{key}"

            # entry = collection.get(entry_id)
            entry = collection.get_by_key(entry_id)
            if not entry or not entry.translation or entry.translation == ps.string:
                continue

            collection_hits += 1

            subrecord = self.plugin.find_string_subrecord(
                ps.form_id, ps.type, ps.string, ps.index
            )
            if subrecord is not None:
                subrecord.set_string(entry.translation)
                updated_count += 1
                subrecord_found += 1
            else:
                subrecord_not_found += 1
                log.warning(
                    f"Subrecord not found for entry: {entry_id!r} "
                    f"(form_id={ps.form_id!r}, type={ps.type!r}, "
                    f"string={ps.string[:40]!r}, index={ps.index!r})"
                )

        log.info(
            f"apply_collection (inline): total={total_strings}, "
            f"collection_hits={collection_hits}, "
            f"subrecord_found={subrecord_found}, "
            f"subrecord_not_found={subrecord_not_found}, "
            f"updated={updated_count}"
        )
        return updated_count

    # ------------------------------------------------------------------
    # Localised path — writes strings files instead of modifying records
    # ------------------------------------------------------------------

    def _apply_localized_collection(self, collection: TranslationEntryCollection) -> int:
        assert self._localized_writer is not None

        updated_count = 0
        last_editor_id: str | None = None
        collection_hits = 0
        localized_count = 0
        inline_count = 0

        dlbr_map = self.plugin._build_dlbr_map()

        # Collect all {PluginStringWithContext: StringSubrecord} across all groups
        all_pairs: dict[PluginStringWithContext, StringSubrecord] = {}
        for group in self.plugin.groups:
            all_pairs.update(
                self.plugin.extract_group_strings_with_context(
                    group,
                    strings_lookup=self.strings_lookup,
                    dlbr_map=dlbr_map,
                )
            )

        ps: PluginStringWithContext
        subrecord: StringSubrecord
        for ps, subrecord in all_pairs.items():
            # Mirror the editor_id fix from PluginParser
            editor_id = ps.editor_id
            if editor_id is None:
                if ps.type and ps.type.replace(" ", ":") != "REFR:FULL":
                    if last_editor_id is not None:
                        editor_id = last_editor_id
            else:
                last_editor_id = editor_id

            index = ps.index if ps.index is not None else 1
            key = ps.type.replace(" ", ":")
            form_id = str(ps.form_id).split("|")[0] if "|" in str(ps.form_id) else str(ps.form_id)
            entry_id = f"{editor_id}:{form_id}|{index}~{key}"

            entry: TranslationEntry | None = collection.get_by_key(entry_id)


            if not entry or not entry.translation or entry.translation == ps.string:
                continue

            collection_hits += 1

            if isinstance(subrecord.string, int):
                # Localised string — route into strings file
                subrecord_type = ps.type.split()[-1] if ps.type else "FULL"
                self._localized_writer.add(subrecord.string, entry.translation, subrecord_type)
                localized_count += 1
            elif isinstance(subrecord.string, RawString):
                # Inline string in an otherwise localised plugin (rare but possible)
                subrecord.set_string(entry.translation)
                inline_count += 1

            updated_count += 1

        log.info(
            f"apply_collection (localised): collection_hits={collection_hits}, "
            f"localized={localized_count}, inline={inline_count}, updated={updated_count}"
        )
        self._inline_count = inline_count
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

