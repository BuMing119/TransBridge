from pathlib import Path
import logging

from sse_plugin_interface.plugin import SSEPlugin
from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection

log = logging.getLogger("PluginWriter")


class PluginWriter:
    """
    将 TranslationEntryCollection 的内容反向写入 SSEPlugin 文件。
    """

    def __init__(self, plugin: SSEPlugin):
        """
        传入已经读取好的 SSEPlugin 实例。
        """
        self.plugin = plugin

    def apply_collection(self, collection: TranslationEntryCollection) -> int:
        """
        根据 TranslationEntryCollection 更新 plugin 字符串。

        :return: 实际更新的字符串数
        """
        updated_count = 0
        last_editor_id = None

        # 统计用于调试
        total_strings = 0
        collection_hits = 0
        subrecord_found = 0
        subrecord_not_found = 0

        for ps in self.plugin.extract_strings():
            total_strings += 1

            # 复现 PluginParser.parse_plugin 中 editor_id 的补全逻辑
            editor_id = ps.editor_id
            if editor_id is None:
                if ps.type and ps.type.replace(" ", ":") != "REFR:FULL":
                    if last_editor_id is not None:
                        editor_id = last_editor_id
            else:
                last_editor_id = editor_id

            # 复现 create_from_plugin_entry 中 index 的规范化（None → 1）
            index = ps.index if ps.index is not None else 1

            key = ps.type.replace(" ", ":")
            form_id = str(ps.form_id).split("|")[0] if "|" in str(ps.form_id) else str(ps.form_id)
            entry_id = f"{editor_id}:{form_id}|{index}~{key}"

            entry = collection.get(entry_id)
            if not entry or not entry.translation or entry.translation == ps.string:
                continue

            collection_hits += 1

            # 用原始字符串（ps.string）定位子记录，再写入译文
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
            f"apply_collection: total={total_strings}, "
            f"collection_hits={collection_hits}, "
            f"subrecord_found={subrecord_found}, "
            f"subrecord_not_found={subrecord_not_found}, "
            f"updated={updated_count}"
        )
        return updated_count

    def write(self, output_path: str | Path) -> None:
        """
        将修改后的插件保存到文件。
        """
        self.plugin.save(Path(output_path))
