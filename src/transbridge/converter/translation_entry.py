from dataclasses import dataclass

from typing import Any
from src.transbridge.parser import EET_Entry
from src.transbridge.parser.xt_parser import XT_Entry
from src.transbridge.parser.plugin.plugin_string_with_context import PluginStringWithContext


@dataclass
class TranslationEntry:
    id: str
    key: str  # 现在存储原来的id值
    original: str
    translation: str
    stage: int
    context: str  # 现在存储原来的key值
    string_id: int | None = None  # 本地化插件的字符串ID，用于精确匹配 strings 文件

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
        form_id = getattr(ps, "form_id", "")


        # 从form_id中提取十六进制ID部分，移除插件文件名
        if "|" in str(form_id):
            form_id = form_id.split("|")[0]

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

        if xt.source != entry.original:
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
        )


