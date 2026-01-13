from dataclasses import dataclass

from typing import Any
from src.transbridge.parser import EET_Entry
from src.transbridge.parser.xt_parser import XT_Entry


@dataclass
class TranslationEntry:
    id: str
    key: str  # 现在存储原来的id值
    original: str
    translation: str
    stage: int
    context: str  # 现在存储原来的key值

    @classmethod
    def create_from_eet_entry(cls, eet_entry: "EET_Entry") -> "TranslationEntry":
        """
        从 EET_Entry 实例创建 TranslationEntry 实例
        :param eet_entry: EET_Entry 实例
        :return: TranslationEntry 实例
        """
        # 检查状态是否为99来决定stage的值
        stage = 1 if eet_entry.status == 99 or eet_entry.traduit else 0

        # 根据edid是否为空来决定id和key的值
        if eet_entry.edid:
            # edid不为空，直接使用edid
            id_value = f"{eet_entry.edid}:{eet_entry.id}"
            key_value = f"{eet_entry.edid}:{eet_entry.id}"
        else:
            # edid为空，使用"None:formid"的格式
            id_value = f"None:{eet_entry.id}"
            key_value = f"None:{eet_entry.id}"
        
        # 构建并返回 TranslationEntry
        return cls(
            id=id_value,  # edid 或 "None:formid"
            key=key_value,  # edid 或 "None:formid"
            original=eet_entry.original,  # original 直接映射
            translation=eet_entry.traduit,  # traduit 直接映射
            stage=stage,  # 根据 status 确定 stage
            context=f"{eet_entry.grup}:{eet_entry.champ}",  # 原来的key值移动到context
        )




    @classmethod
    def creat_from_plugin_entry(cls, ps: "PluginString") -> "TranslationEntry":
        """
        从 PluginString 实例创建 TranslationEntry 实例
        :param ps: PluginString 实例（sse_plugin_interface.plugin_string.PluginString）
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

        id_value = f"{editor_id}:{form_id}"

        index= getattr(ps, "index", "")

        return cls(
            id=id_value,
            key=id_value,  # 将原来的id值复制到key
            original=getattr(ps, "string", "") or "",
            translation="",
            stage=0,
            context=original_key  # 原来的key值移动到context
        )

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

        # ---------- 1. 根据 list_id + edid 匹配 id ----------

        # TranslationEntry.id 形如 "a:b"
        id_left, _, id_right = entry.id.partition(":")

        if xt.list_id == 0:
            # edid == id 前半部分
            if xt.edid != id_left:
                return None

        elif xt.list_id == 1:
            # edid == [id 后半部分]
            if xt.edid != f"[{id_right}]":
                return None

        else:
            # 未定义的 list_id，直接不匹配
            return None

        # ---------- 2. rec / source 的一致性校验（防误匹配） ----------

        # 注意：现在原来的key值存储在context中
        if xt.rec != entry.context:
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
            "context": self.context
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
        )


