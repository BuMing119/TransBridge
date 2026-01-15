from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
import re

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


def export_to_categorized_json_files(
    collection: TranslationEntryCollection ,
    output_dir: str | Path,
    *,
    ensure_ascii: bool = False,
    indent: int = 2,
) -> None:
    """
    根据context规则将翻译条目分类到不同的JSON文件中。

    对于INFO和DIAL类型，会根据quest_formid分组到单独的文件中。

    分类规则：
    - 书籍_书名.json: "BOOK:FULL"
    - 书籍_内容.json: "BOOK:DESC"
    - 互动.json: "FLOR:RNAM", "FURN:FULL", "HAZD:FULL"
    - 人名.json: "NPC_:FULL", "NPC_:SHRT", "TACT:FULL"
    - 任务日志.json: "QUST:FULL", "QUST:NNAM"
    - 地名与门.json: "CELL:FULL", "DOOR:FULL", "LCTN:FULL", "REFR:FULL", "WRLD:FULL"
    - 法术_龙吼_技能.json: "ENCH:FULL", "EXPL:FULL", "MESG:DESC", "MESG:FULL", "MESG:ITXT", "MGEF:DNAM", "MGEF:FULL", "PERK:FULL", "SHOU:FULL", "SPEL:DESC", "SPEL:FULL"
    - 物品.json: "ACTI:FULL", "ACTI:RNAM", "ALCH:FULL", "AMMO:FULL", "ARMO:DESC", "ARMO:FULL", "CONT:FULL", "INGR:FULL", "KEYM:FULL", "MISC:FULL", "SLGM:FULL", "TREE:FULL", "WEAP:DESC", "WEAP:FULL"

    :param collection: TranslationEntryCollection 实例
    :param output_dir: 输出目录路径
    :param ensure_ascii: 是否确保ASCII编码
    :param indent: JSON缩进空格数
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 定义分类规则
    category_rules = {
        "书籍_书名.json": ["BOOK:FULL"],
        "书籍_内容.json": ["BOOK:DESC"],
        "互动.json": ["FLOR:RNAM", "FURN:FULL", "HAZD:FULL"],
        "人名.json": ["NPC_:FULL", "NPC_:SHRT", "TACT:FULL"],
        "任务日志.json": ["QUST:FULL", "QUST:NNAM"],
        "地名与门.json": ["CELL:FULL", "DOOR:FULL", "LCTN:FULL", "REFR:FULL", "WRLD:FULL"],
        "法术_龙吼_技能.json": ["ENCH:FULL", "EXPL:FULL", "MESG:DESC", "MESG:FULL", "MESG:ITXT", "MGEF:DNAM", "MGEF:FULL", "PERK:FULL", "SHOU:FULL", "SPEL:DESC", "SPEL:FULL"],
        "物品.json": ["ACTI:FULL", "ACTI:RNAM", "ALCH:FULL", "AMMO:FULL", "ARMO:DESC", "ARMO:FULL", "CONT:FULL", "INGR:FULL", "KEYM:FULL", "MISC:FULL", "SLGM:FULL", "TREE:FULL", "WEAP:DESC", "WEAP:FULL"]
    }

    # 用于存储按文件名分组的条目
    categorized_entries: dict[str, list[TranslationEntry]] = {
        filename: [] for filename in category_rules.keys()
    }

    # 用于存储INFO和DIAL类型的条目，按quest_formid分组
    dial_entries: dict[str, list[TranslationEntry]] = defaultdict(list)

    # 遍历所有条目进行分类
    for entry in collection._entries.values():
        # 处理INFO和DIAL类型
        if "|" in entry.context:
            # 提取context和quest_formid
            context_part, quest_formid = entry.context.split("|", 1)
            # 使用quest_formid作为文件名的一部分
            dial_entries[quest_formid].append(entry)
        else:
            # 处理普通类型
            for filename, contexts in category_rules.items():
                if entry.context in contexts:
                    categorized_entries[filename].append(entry)
                    break

    # 将普通分类的条目保存到JSON文件
    for filename, entries in categorized_entries.items():
        if entries:  # 只保存非空文件
            file_path = output_dir / filename
            temp_collection = collection.__class__(entries)
            temp_collection.to_json_file(file_path, ensure_ascii=ensure_ascii, indent=indent)

    # 将INFO和DIAL类型的条目保存到单独的JSON文件
    for quest_formid, entries in dial_entries.items():
        # 查找 quest_formid 对应的 TranslationEntry
        quest_entry = None
        for entry in collection:
            # 检查 id 的后半部分是否匹配 quest_formid
            id_parts = entry.id.split(':')  # 将 id 按照 ':' 分割
            if len(id_parts) > 1:
                form_id_and_index = id_parts[1]  # 获取 "form_id|index" 部分
                form_id = form_id_and_index.split('|')[0]  # 获取 form_id

                # 如果 form_id 匹配 quest_formid，则认为找到对应的 TranslationEntry
                if form_id == quest_formid:
                    quest_entry = entry
                    break

        # 使用 quest_entry 的 original 字段作为文件名，如果找不到则使用 quest_formid
        quest_original = quest_entry.original if quest_entry else quest_formid

        # 清理文件名中的非法字符
        quest_original = re.sub(r'[<>:"/\|?*]', '_', quest_original)

        filename = f"对话_[{quest_original}].json"
        file_path = output_dir / filename
        temp_collection = collection.__class__(entries)
        temp_collection.to_json_file(file_path, ensure_ascii=ensure_ascii, indent=indent)
