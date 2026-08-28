"""
record type 分类常量（全项目共享）。

定义 Skyrim ESP record type 的语义分类，供 AI 翻译批次规划、导出分文件等模块复用。
若未来支持其他游戏，在此处扩展即可。
"""

# ── 第一轮：固定类别（专有名词，适合批量翻译并写入术语库）─────────────────────
ROUND1_CATEGORIES: dict[str, set[str]] = {
    "种族与派系": {"RACE:FULL", "RACE:DESC", "CLAS:FULL"},
    "人名": {"NPC_:FULL", "NPC_:SHRT", "TACT:FULL"},
    "地名": {"LCTN:FULL", "WRLD:FULL", "CELL:FULL", "DOOR:FULL", "REFR:FULL"},
    "书名": {"BOOK:FULLSCRL:FULL"},
    "物品": {
        "ACTI:FULL",
        "ACTI:RNAM",
        "ALCH:FULL",
        "AMMO:FULL",
        "ARMO:DESC",
        "ARMO:FULL",
        "CONT:FULL",
        "INGR:FULL",
        "KEYM:FULL",
        "MISC:FULL",
        "SLGM:FULL",
        "TREE:FULL",
        "WEAP:DESC",
        "WEAP:FULL",
        "FLOR:FULL",
        "AMMO:DESC",
        "PROJ:FULL",
    },
    "法术技能": {
        "ENCH:FULL",
        "EXPL:FULL",
        "MESG:DESC",
        "MESG:FULL",
        "MESG:ITXT",
        "MGEF:DNAM",
        "MGEF:FULL",
        "PERK:FULL",
        "PERK:DESC",
        "PERK:EPF2",
        "SHOU:FULL",
        "SHOU:DESC",
        "SPEL:DESC",
        "SPEL:FULL",
        "AVIF:FULL",
        "AVIF:DESC",
        "PERK:EPFD",
    },
    "任务名": {"QUST:FULL"},
    "互动": {"FLOR:RNAM", "FURN:FULL", "HAZD:FULL"},
}

# ── 第二轮：对话类（INFO/DIAL），按 quest_formid 分组 ─────────────────────────
ROUND2_PREFIXES: set[str] = {"INFO", "DIAL"}

# ── 第三轮：长文本（书籍内容/任务日志等）────────────────────────────────────────
ROUND3_CONTEXTS: set[str] = {"BOOK:DESC", "QUST:NNAM", "QUST:CNAM", "LSCR:DESC", "SCRL:DESC"}

# ── 翻译完成后自动写入动态术语库的 context 集合（第一轮里的专有名词类型）────────
AUTO_TERM_CONTEXTS: set[str] = {
    "NPC_:FULL",
    "NPC_:SHRT",
    "TACT:FULL",
    "LCTN:FULL",
    "WRLD:FULL",
    "CELL:FULL",
    "DOOR:FULL",
    "REFR:FULL",
    "BOOK:FULL",
    "RACE:FULL",
}

# User-facing categories shared by the Workbench filter and mixed-rule editor.
DISPLAY_CATEGORY_CONTEXTS: dict[str, set[str]] = {
    "人名": {"NPC_:FULL", "NPC_:SHRT", "TACT:FULL"},
    "地名": {"CELL:FULL", "DOOR:FULL", "LCTN:FULL", "REFR:FULL", "WRLD:FULL"},
    "书名": {"BOOK:FULL", "SCRL:FULL"},
    "书籍内容": {"BOOK:DESC", "SCRL:DESC"},
    "互动": {"FLOR:RNAM", "FURN:FULL", "HAZD:FULL"},
    "任务日志": {"QUST:FULL", "QUST:NNAM", "QUST:CNAM"},
    "法术技能": set(ROUND1_CATEGORIES["法术技能"]),
    "物品": set(ROUND1_CATEGORIES["物品"]),
}
ALL_DISPLAY_CATEGORIES = (
    "人名",
    "地名",
    "书名",
    "书籍内容",
    "物品",
    "法术技能",
    "对话",
    "互动",
    "任务日志",
    "其他",
)
_DISPLAY_CATEGORY_BY_CONTEXT = {
    context: category for category, contexts in DISPLAY_CATEGORY_CONTEXTS.items() for context in contexts
}


def context_category(context: str) -> str:
    """Return the stable user-facing category for a raw translation context."""

    base = (context or "").split("|", 1)[0]
    record = base.split(":", 1)[0]
    if record in ROUND2_PREFIXES:
        return "对话"
    return _DISPLAY_CATEGORY_BY_CONTEXT.get(base, "其他")


# ── 导出分文件规则（文件名 → context 列表）───────────────────────────────────
# 对话类（INFO/DIAL）由导出逻辑按 quest_formid 动态命名，不在此处定义。
EXPORT_CATEGORIES: dict[str, list[str]] = {
    "书籍_书名.json": ["BOOK:FULL", "SCRL:FULL"],
    "书籍_内容.json": ["BOOK:DESC", "SCRL:DESC"],
    "互动.json": ["FLOR:RNAM", "FURN:FULL", "HAZD:FULL"],
    "人名.json": ["NPC_:FULL", "NPC_:SHRT", "TACT:FULL"],
    "任务日志.json": ["QUST:FULL", "QUST:NNAM", "QUST:CNAM"],
    "地名与门.json": ["CELL:FULL", "DOOR:FULL", "LCTN:FULL", "REFR:FULL", "WRLD:FULL"],
    "种族.json": ["RACE:FULL", "RACE:DESC", "CLAS:FULL"],
    "法术_龙吼_技能.json": [
        "ENCH:FULL",
        "EXPL:FULL",
        "MESG:DESC",
        "MESG:FULL",
        "MESG:ITXT",
        "MGEF:DNAM",
        "MGEF:FULL",
        "PERK:FULL",
        "PERK:DESC",
        "PERK:EPF2",
        "SHOU:FULL",
        "SHOU:DESC",
        "SPEL:DESC",
        "SPEL:FULL",
        "AVIF:FULL",
        "AVIF:DESC",
        "PERK:EPFD",
    ],
    "物品.json": [
        "ACTI:FULL",
        "ACTI:RNAM",
        "ALCH:FULL",
        "AMMO:FULL",
        "ARMO:DESC",
        "ARMO:FULL",
        "CONT:FULL",
        "INGR:FULL",
        "KEYM:FULL",
        "MISC:FULL",
        "SLGM:FULL",
        "TREE:FULL",
        "WEAP:DESC",
        "WEAP:FULL",
        "PROJ:FULL",
        "FLOR:FULL",
    ],
    "过场.json": ["LSCR:DESC"],
}
