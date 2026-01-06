from dataclasses import dataclass

from src.transbridge.parser import EET_Entry


@dataclass
class TranslationEntry:
    id: str
    key: str
    original: str
    translation: str
    stage: int
    context: str

    @classmethod
    def creat_from_eet_entry(cls, eet_entry: "EET_Entry") -> "TranslationEntry":
        """
        从 EET_Entry 实例创建 TranslationEntry 实例
        :param eet_entry: EET_Entry 实例
        :return: TranslationEntry 实例
        """
        # 检查状态是否为99来决定stage的值
        stage = 1 if eet_entry.status == 99 else 0
        # 构建并返回 TranslationEntry
        return cls(
            id=eet_entry.edid,  # edid 对应 id
            key=f"{eet_entry.grup}:{eet_entry.champ}",  # grup:champ 作为 key
            original=eet_entry.original,  # original 直接映射
            translation=eet_entry.traduit,  # traduit 直接映射
            stage=stage,  # 根据 status 确定 stage
            context=None,  # 默认 context 为 None
        )




    @classmethod
    def creat_from_plugin_entry(cls, ps: "PluginString") -> "TranslationEntry":
        """
        从 PluginString 实例创建 TranslationEntry 实例
        :param ps: PluginString 实例（sse_plugin_interface.plugin_string.PluginString）
        :return: TranslationEntry 实例
        """
        # "INFO NAM1" -> "INFO:NAM1"
        key = ps.type.replace(" ", ":") if getattr(ps, "type", None) else "UNKNOWN"

        # 参考 PluginParser._create_item：使用 editor_id + form_id 组成唯一 id
        editor_id = getattr(ps, "editor_id", "")
        form_id = getattr(ps, "form_id", "")

        return cls(
            id=f"{editor_id}:{form_id}",
            key=key,
            original=getattr(ps, "string", "") or "",
            translation="",
            stage=0,
            context=None,
        )


