from pathlib import Path
from typing import Iterable

from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.plugin_string import PluginString
from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


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
        modified_strings: list[PluginString] = []
        updated_count = 0

        for ps in self.plugin.extract_strings():
            # 构造 TranslationEntry.id = editor_id:form_id
            entry_id = f"{ps.editor_id}:{ps.form_id}"
            entry = collection.get(entry_id)
            if not entry:
                continue

            # 匹配 key（插件中 "x y" → entry中 "x:y"）
            key = ps.type.replace(" ", ":")
            # 注意：现在原来的key值存储在context中
            if key != entry.context:
                continue

            # 如果没有翻译内容则跳过
            if not entry.translation:
                continue

            # 若 translation 与原始ps.string一致则不必更新
            if entry.translation == ps.string:
                continue

            # 构造替换用 PluginString
            modified = PluginString(
                editor_id=ps.editor_id,
                form_id=ps.form_id,
                index=ps.index,
                type=ps.type,
                string=entry.translation,
            )

            modified_strings.append(modified)
            updated_count += 1

        # 批量替换
        if modified_strings:
            self.plugin.replace_strings(modified_strings)

        return updated_count

    def write(self, output_path: str | Path) -> None:
        """
        将修改后的插件保存到文件。
        """
        self.plugin.save(Path(output_path))
