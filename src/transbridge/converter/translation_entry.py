from dataclasses import dataclass

from typing import Any
from src.transbridge.parser import EET_Entry
from src.transbridge.parser.xt_parser import XT_Entry
from src.transbridge.parser.plugin.plugin_string_with_context import PluginStringWithContext


def _normalize_text(s: str) -> str:
    """规范化文本空白：统一换行符为 \\n，去除首尾空白。"""
    return s.replace('\r\n', '\n').replace('\r', '\n').strip()


@dataclass
class TranslationEntry:
    id: str
    key: str  # 现在存储原来的id值
    original: str
    translation: str
    stage: int
    context: str  # 现在存储原来的key值
    string_id: int | None = None  # 本地化插件的字符串ID，用于精确匹配 strings 文件
    form_id_with_plugin: str | None = None  # 完整的 FormID|BaseRecordPlugin 格式，用于导出新JSON格式

    # DSD 兼容字段（解析时直接保存，用于 DSD 格式双向转换）
    dsd_type: str = ""           # DSD 类型格式："NPC_ FULL" / "INFO NAM1"（空格分隔）
    dsd_index: int = 1           # 原始索引
    editor_id: str = ""          # 原始 editor_id

    @staticmethod
    def _build_eet_id(edid: str | None, form_id: str, index: int, grup: str, champ: str) -> str:
        """构建 EET 来源条目的唯一 id，格式：{edid|None}:{form_id}|{index}~{grup}:{champ}"""
        prefix = edid if edid else "None"
        return f"{prefix}:{form_id}|{index}~{grup}:{champ}"

    @classmethod
    def create_from_eet_entry(cls, eet_entry: "EET_Entry") -> "TranslationEntry":
        """
        从 EET_Entry 实例创建 TranslationEntry 实例
        :param eet_entry: EET_Entry 实例
        :return: TranslationEntry 实例
        """
        stage = 1 if eet_entry.status == 99 or eet_entry.traduit else 0
        id_value = cls._build_eet_id(eet_entry.edid, eet_entry.id, eet_entry.index, eet_entry.grup, eet_entry.champ)

        return cls(
            id=id_value,
            key=id_value,
            original=eet_entry.original,
            translation=eet_entry.traduit,
            stage=stage,
            context=f"{eet_entry.grup}:{eet_entry.champ}",
        )




    @classmethod
    def create_from_plugin_entry(cls, ps: "PluginStringWithContext") -> "TranslationEntry":
        """
        从 PluginStringWithContext 实例创建 TranslationEntry 实例
        :param ps: PluginStringWithContext 实例
        :return: TranslationEntry 实例
        """
        # "INFO NAM1" -> "INFO:NAM1"
        original_key = ps.type.replace(" ", ":") if getattr(ps, "type", None) else "UNKNOWN"

        # 参考 PluginParser._create_item：使用 editor_id + form_id 组成唯一 id
        editor_id = getattr(ps, "editor_id", "")
        form_id_raw = getattr(ps, "form_id", "")

        # 保存完整的 form_id（包含插件名）
        full_form_id = form_id_raw if "|" in str(form_id_raw) else None

        # 从form_id中提取十六进制ID部分，移除插件文件名
        if "|" in str(form_id_raw):
            form_id = form_id_raw.split("|")[0]
        else:
            form_id = form_id_raw

        if ps.index is None:
            ps.index = 1

        id_value = f"{editor_id}:{form_id}|{ps.index}~{original_key}"
        if original_key.split(":")[0] == "INFO" or original_key.split(":")[0] == "DIAL":
            quest_formid_ori = getattr(ps.context, "quest", None) or ""
            quest_formid = quest_formid_ori.split("|")[0]

            #original_key = f"{original_key}|{getattr(ps, 'quest_formid', '')}"
            original_key = f"{original_key}|{quest_formid}"



        return cls(
            id=id_value,
            key=id_value,  # 将原来的id值复制到key
            original=getattr(ps, "string", "") or "",
            translation="",
            stage=0,
            context=original_key,  # 原来的key值移动到context
            string_id=getattr(ps, "string_id", None),  # 传递 string_id
            form_id_with_plugin=full_form_id,  # 保存完整的 form_id
            # DSD 兼容字段
            dsd_type=getattr(ps, "type", "") or "",  # "NPC_ FULL" 格式
            dsd_index=getattr(ps, "index", 1) or 1,  # 原始索引，默认为 1
            editor_id=getattr(ps, "editor_id", "") or "",  # 原始 editor_id
        )

    @classmethod
    def try_update_from_xt(
            cls,
            entry: "TranslationEntry",
            xt: XT_Entry,
    ) -> "TranslationEntry | None":
        """
        尝试用 XT_Entry 更新已有的 TranslationEntry。
        - 不匹配则返回 None
        - 匹配但不更新则返回原 entry
        - 匹配且满足条件则返回更新后的新 entry
        """

        # ---------- 1. edid 匹配 ----------

        # TranslationEntry.id 形如 "edid:form_id|index~TYPE:FIELD"
        id_left, _, id_right_with_index = entry.id.partition(":")
        id_right, _, id_index_with_type = id_right_with_index.partition("|")
        id_index, _, id_type = id_index_with_type.partition("~")

        # XT 工具对缺 EditorID 的记录处理不固定：
        # list_id=0 可能填 editid 也可能填 bare formid；list_id=1 同理。
        # 不依赖 list_id 判断 edid 格式，改为检查三种合理候选形式。
        valid_edids = {id_left, id_right, f"[{id_right}]"}
        if xt.edid not in valid_edids:
            return None

        # ---------- 1.5 检查 index 是否匹配 ----------
        entry_index = int(id_index) if id_index else None
        if entry_index is not None and entry_index != xt.index:
            return None

        # ---------- 2. rec / source 的一致性校验（防误匹配） ----------

        # entry.context 对 INFO/DIAL 含 quest_formid 后缀（"INFO:NAM1|quest"）
        # XT 的 rec 只含基础部分（"INFO:NAM1"），取 context 基础部分比较
        context_base = entry.context.split("|")[0] if entry.context else ""
        if xt.rec != context_base:
            return None

        if _normalize_text(xt.source) != _normalize_text(entry.original):
            return None

        # ---------- 3. 判断是否满足“更新 translation 的条件” ----------

        should_update = (
                entry.stage == 0
                and not entry.translation
                and bool(xt.dest)
        )

        if not should_update:
            return entry

        # ---------- 4. 返回更新后的新实例 ----------

        return cls(
            id=entry.id,
            key=entry.key,
            original=entry.original,
            translation=xt.dest,
            stage=1,
            context=entry.context,
            form_id_with_plugin=entry.form_id_with_plugin,
            string_id=entry.string_id,
            # 保留 DSD 字段
            dsd_type=entry.dsd_type,
            dsd_index=entry.dsd_index,
            editor_id=entry.editor_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """
        将 TranslationEntry 转为可序列化 dict。
        """
        return {
            "id": self.id,
            "key": self.key,
            "original": self.original,
            "translation": self.translation,
            "stage": self.stage,
            "context": self.context,
            "string_id": self.string_id,
            "full_form_id": self.form_id_with_plugin,
            # DSD 兼容字段
            "dsd_type": self.dsd_type,
            "dsd_index": self.dsd_index,
            "dsd_editor_id": self.editor_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TranslationEntry":
        """
        从 dict 恢复 TranslationEntry。
        """
        return cls(
            id=data["id"],
            key=data.get("key", ""),
            original=data.get("original", ""),
            translation=data.get("translation", ""),
            stage=data.get("stage", 0),
            context=data.get("context"),
            string_id=data.get("string_id"),
            form_id_with_plugin=data.get("full_form_id"),
            # DSD 兼容字段（向后兼容：旧 JSON 无这些字段时使用默认值）
            dsd_type=data.get("dsd_type", ""),
            dsd_index=data.get("dsd_index", 1),
            editor_id=data.get("dsd_editor_id", ""),
        )

    # ==================== DSD 格式转换 ====================

    # DSD 索引类型集合（需要 index 字段的类型）
    DSD_INDEX_TYPES = frozenset({
        "INFO NAM1", "QUST NNAM", "MESG ITXT", "PERK EPF2", "PERK EPFD"
    })

    def to_dsd_dict(self) -> dict[str, Any]:
        """
        导出为 DSD 格式字典。

        DSD 格式有三种变体：
        1. 基础格式：form_id, type, string（大多数类型）
        2. QUST CNAM：form_id, type, original, string
        3. 索引格式：form_id, type, index, string（INFO NAM1 等）
        4. GMST DATA：form_id, editor_id, type, string
        """
        form_id = self.form_id_with_plugin or ""
        type_str = self.dsd_type  # 已是 "NPC_ FULL" 格式

        # 没有译文时不导出
        if not self.translation:
            return {}

        base = {"form_id": form_id, "type": type_str}

        # GMST DATA: 需要 editor_id
        if type_str == "GMST DATA":
            return {**base, "editor_id": self.editor_id, "string": self.translation}

        # QUST CNAM: 需要 original 字段
        if type_str == "QUST CNAM":
            return {**base, "original": self.original, "string": self.translation}

        # 索引类型: INFO NAM1, QUST NNAM, MESG ITXT, PERK EPF2, PERK EPFD
        if type_str in self.DSD_INDEX_TYPES:
            return {**base, "index": self.dsd_index, "string": self.translation}

        # 基础类型
        return {**base, "string": self.translation}

    @classmethod
    def from_dsd_dict(cls, data: dict[str, Any]) -> "TranslationEntry":
        """
        从 DSD 格式字典创建 TranslationEntry。

        :param data: DSD 格式字典，包含 form_id, type, string 等字段
        :return: TranslationEntry 实例
        """
        form_id = data["form_id"]
        type_str = data["type"]
        string = data.get("string", "")

        # 提取 form_id 部分（去掉 |Plugin 后缀）
        form_id_hex = form_id.split("|")[0]

        # 获取可选字段
        editor_id = data.get("editor_id", "")
        index = data.get("index", 1)

        # 构建 context（冒号格式："NPC_:FULL"）
        type_colon = type_str.replace(" ", ":")

        # 构建 id
        id_value = f"{editor_id}:{form_id_hex}|{index}~{type_colon}"

        return cls(
            id=id_value,
            key=id_value,
            original=data.get("original", ""),  # QUST CNAM 可能有
            translation=string,
            stage=1 if string else 0,
            context=type_colon,
            form_id_with_plugin=form_id,
            dsd_type=type_str,
            dsd_index=index,
            editor_id=editor_id,
        )


