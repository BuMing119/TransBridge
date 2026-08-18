from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    FormatId,
    ParatranzJsonAdapter,
    SourceDescriptor,
    WriteRequest,
)
from transbridge.converter.context_categories import EXPORT_CATEGORIES
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def export_to_categorized_json_files(
    collection: TranslationEntryCollection,
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
    - 法术_龙吼_技能.json: "ENCH:FULL", "EXPL:FULL", "MESG:DESC", "MESG:FULL", "MESG:ITXT", "MGEF:DNAM",
      "MGEF:FULL", "PERK:FULL", "SHOU:FULL", "SPEL:DESC", "SPEL:FULL"
    - 物品.json: "ACTI:FULL", "ACTI:RNAM", "ALCH:FULL", "AMMO:FULL", "ARMO:DESC", "ARMO:FULL", "CONT:FULL",
      "INGR:FULL", "KEYM:FULL", "MISC:FULL", "SLGM:FULL", "TREE:FULL", "WEAP:DESC", "WEAP:FULL"

    :param collection: TranslationEntryCollection 实例
    :param output_dir: 输出目录路径
    :param ensure_ascii: 是否确保ASCII编码
    :param indent: JSON缩进空格数
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 定义分类规则
    category_rules = EXPORT_CATEGORIES

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
            _write_paratranz_entries(entries, file_path, ensure_ascii=ensure_ascii, indent=indent)

    # 将INFO和DIAL类型的条目保存到单独的JSON文件
    for quest_formid, entries in dial_entries.items():
        filename = _dial_filename(quest_formid, collection)
        file_path = output_dir / filename
        _write_paratranz_entries(entries, file_path, ensure_ascii=ensure_ascii, indent=indent)


def _write_paratranz_entries(
    entries: list[TranslationEntry],
    file_path: Path,
    *,
    ensure_ascii: bool,
    indent: int,
) -> None:
    """Compatibility facade over the V2 offline ParaTranz writer."""
    result = ParatranzJsonAdapter().write(
        WriteRequest(
            SourceDescriptor(str(file_path), file_path.name, media_type="application/json"),
            FormatId.JSON_PARATRANZ,
            tuple(entries),
            0,
            RequestContext("legacy-categorized-paratranz-export"),
            new_template=b"",
            options=(("ensure_ascii", ensure_ascii), ("indent", indent)),
        )
    )
    if result.outcome is not OperationOutcome.COMPLETED:
        messages = "; ".join(diagnostic.message for diagnostic in result.diagnostics)
        raise ValueError(messages or "Unable to write ParaTranz JSON.")


def _dial_filename(quest_formid: str, collection: TranslationEntryCollection) -> str:
    """根据 quest_formid 查找任务名，生成对话文件名。"""
    quest_entry = None
    for entry in collection:
        key_parts = entry.key.split(':')
        if len(key_parts) > 1:
            form_id = key_parts[1].split('|')[0]
            if form_id == quest_formid:
                quest_entry = entry
                break
    quest_original = quest_entry.original if quest_entry else quest_formid
    # 清理非法字符并截断长度（避免 Windows 路径过长）
    quest_original = re.sub(r'[<>:"/\\|?*]', '_', quest_original)
    max_len = 80  # 限制任务名长度
    if len(quest_original) > max_len:
        quest_original = quest_original[:max_len] + "..."
    return f"对话_[{quest_original}].json"


def get_categorized_file_names(
    collection: TranslationEntryCollection,
) -> list[tuple[str, int]]:
    """
    预计算分类上传时会生成的文件列表及各文件的词条数，不写入磁盘。

    Returns:
        list of (filename, entry_count)，按文件名排序，只含非空分类。
    """
    category_rules = EXPORT_CATEGORIES

    counts: dict[str, int] = {filename: 0 for filename in category_rules}
    dial_counts: dict[str, int] = defaultdict(int)

    for entry in collection._entries.values():
        if "|" in entry.context:
            _, quest_formid = entry.context.split("|", 1)
            dial_counts[quest_formid] += 1
        else:
            for filename, contexts in category_rules.items():
                if entry.context in contexts:
                    counts[filename] += 1
                    break

    result: list[tuple[str, int]] = []
    for filename, count in counts.items():
        if count > 0:
            result.append((filename, count))

    for quest_formid, count in dial_counts.items():
        filename = _dial_filename(quest_formid, collection)
        result.append((filename, count))

    result.sort(key=lambda x: x[0])
    return result
