from dataclasses import dataclass, field, replace
from typing import Any
import warnings

from transbridge.application.io.identity import (
    EntryKey,
    EntryRevision,
    ExternalEntryRef,
    Provenance,
    SourceNamespace,
)
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.converter.plugin_entry_metadata import build_plugin_metadata
from transbridge.parser import EET_Entry
from transbridge.parser.plugin.plugin_string_with_context import PluginStringWithContext
from transbridge.parser.xt import SST_Entry, XT_Entry


def _normalize_text(s: str) -> str:
    """规范化文本空白：统一换行符为 \\n，去除首尾空白。"""
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


# Stage constants — aligned with ParaTranz platform
STAGE_UNTRANSLATED = 0  # 未翻译
STAGE_TRANSLATED = 1  # 已翻译
STAGE_QUESTIONABLE = 2  # 有疑问
STAGE_CHECKED = 3  # 已检查
STAGE_REVIEWED = 5  # 已审核（未开启二次校对的项目审核词条时直接设为此状态）
STAGE_LOCKED = 9  # 已锁定（仅管理员可解锁，词条强制按译文导出）
STAGE_HIDDEN = -1  # 已隐藏（词条强制按原文导出）

STAGE_LABELS: dict[int, str] = {
    0: "未翻译",
    1: "已翻译",
    2: "有疑问",
    3: "已检查",
    5: "已审核",
    9: "已锁定",
    -1: "已隐藏",
}

STAGE_COLORS: dict[int, str] = {
    0: "#9E9E9E",
    1: "#2196F3",
    2: "#FF9800",
    3: "#00BCD4",
    5: "#4CAF50",
    9: "#B71C1C",
    -1: "#616161",
}


@dataclass
class TranslationEntry:
    id: str  # 历史遗留字段：ADR-002（2026-05-18）后不再作为主索引，语义见 key/context
    key: str  # 唯一主索引（EditorID:FormID|index~context 复合键），TranslationEntryCollection._entries 的主键
    original: str
    translation: str
    stage: int
    context: str | None  # 条目上下文分类（NPC_:FULL / INFO NAM1 等）
    string_id: int | None = None  # 本地化插件的字符串ID，用于精确匹配 strings 文件
    form_id_with_plugin: str | None = None  # 完整的 FormID|BaseRecordPlugin 格式，用于导出新JSON格式

    # DSD 兼容字段（解析时直接保存，用于 DSD 格式双向转换）
    dsd_type: str = ""  # DSD 类型格式："NPC_ FULL" / "INFO NAM1"（空格分隔）
    dsd_index: int = 1  # 原始索引
    editor_id: str = ""  # 原始 editor_id

    # V2 identity envelope. ``id`` and ``key`` above remain read-compatible facades.
    entry_key: EntryKey | None = None
    external_refs: tuple[ExternalEntryRef, ...] = ()
    revision: EntryRevision = field(default_factory=EntryRevision)
    provenance: tuple[Provenance, ...] = ()
    metadata: tuple[tuple[str, Any], ...] = ()
    _initialized: bool = field(default=False, init=False, repr=False, compare=False)

    SCHEMA_VERSION = 2
    _IDENTITY_FIELDS = frozenset({"id", "key", "entry_key", "external_refs", "revision", "provenance", "metadata"})
    _LEGACY_MUTABLE_FIELDS = frozenset({"original", "translation", "stage", "context"})

    def __post_init__(self) -> None:
        local_key = str(self.key or self.id)
        entry_key = self.entry_key or EntryKey(SourceNamespace.legacy(), local_key)
        if self.key and self.key != entry_key.local_key:
            raise ValueError("legacy key facade must match EntryKey.local_key")
        refs = tuple(self.external_refs)
        if len({item.index_key for item in refs}) != len(refs):
            raise ValueError("external_refs must not contain duplicate identities")
        revision = self.revision if isinstance(self.revision, EntryRevision) else EntryRevision(self.revision)
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "key", entry_key.local_key)
        object.__setattr__(self, "entry_key", entry_key)
        object.__setattr__(self, "external_refs", refs)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        object.__setattr__(self, "metadata", tuple(self.metadata))
        object.__setattr__(self, "_initialized", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if self.__dict__.get("_initialized", False) and name in self._IDENTITY_FIELDS:
            if getattr(self, name) != value:
                raise AttributeError(f"{name} is V2 identity state; use CollectionMutationPort.apply()")
        if self.__dict__.get("_initialized", False) and name in self._LEGACY_MUTABLE_FIELDS:
            if getattr(self, name) != value:
                warnings.warn(
                    f"Direct TranslationEntry.{name} mutation is deprecated; use CollectionMutationPort.apply()",
                    DeprecationWarning,
                    stacklevel=2,
                )
        super().__setattr__(name, value)

    def snapshot(self) -> EntrySnapshot:
        entry_key = self.identity
        return EntrySnapshot(
            entry_key=entry_key,
            legacy_id=self.id,
            original=self.original,
            translation=self.translation,
            stage=self.stage,
            context=self.context,
            external_refs=self.external_refs,
            revision=self.revision,
            provenance=self.provenance,
            metadata=self.metadata,
        )

    @property
    def identity(self) -> EntryKey:
        if self.entry_key is None:
            raise RuntimeError("TranslationEntry identity was not initialized")
        return self.entry_key

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
        stage = STAGE_TRANSLATED if eet_entry.status == 99 or eet_entry.traduit else STAGE_UNTRANSLATED
        id_value = cls._build_eet_id(eet_entry.edid, eet_entry.id, eet_entry.index, eet_entry.grup, eet_entry.champ)

        return cls(
            id=id_value,
            key=id_value,
            original=eet_entry.original,
            translation=eet_entry.traduit,
            stage=stage,
            context=f"{eet_entry.grup}:{eet_entry.champ}",
        )

    # REC 后缀均为 4 字符（FULL/NAM1/DESC/DNAM/ITXT/NNAM/RNAM/SHRT 等）
    _REC_SUFFIXES = (
        "FULL",
        "NAM1",
        "NAM2",
        "DATA",
        "DESC",
        "NAME",
        "GOLD",
        "SNAM",
        "QNAM",
        "CNAM",
        "EDID",
        "MODL",
        "MODT",
        "DNAM",
        "ITXT",
        "NNAM",
        "RNAM",
        "SHRT",
    )

    @staticmethod
    def _rec_display(rec: str) -> str:
        """将拼接 REC 还原为冒号格式：QUSTNNAM → QUST:NNAM"""
        suffix = rec[-4:]
        if suffix in TranslationEntry._REC_SUFFIXES:
            return f"{rec[:-4]}:{suffix}"
        return rec  # fallback: 无法识别则保持原样

    @classmethod
    def create_from_sst_entry(cls, sst_entry: "SST_Entry") -> "TranslationEntry":
        """
        从 SST_Entry 实例创建 TranslationEntry 实例
        :param sst_entry: SST_Entry 实例
        :return: TranslationEntry 实例
        """
        id_value = f"{sst_entry.rec}:{sst_entry.form_id:08X}|{sst_entry.index}"
        rec = cls._rec_display(sst_entry.rec)
        translation = sst_entry.translated_text if sst_entry.translated_text else ""
        stage = STAGE_TRANSLATED if translation else STAGE_UNTRANSLATED
        return cls(
            id=id_value,
            key=id_value,
            original=sst_entry.text,
            translation=translation,
            stage=stage,
            context=rec,
            # f2 = FNV-1a(editor_id) for named entries; grouping key for bare-FormID entries
            editor_id=f"0x{sst_entry.f2:08X}" if sst_entry.f2 else "",
        )

    @classmethod
    def create_from_plugin_entry(
        cls,
        ps: "PluginStringWithContext",
        *,
        source_order: int | None = None,
    ) -> "TranslationEntry":
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

            # original_key = f"{original_key}|{getattr(ps, 'quest_formid', '')}"
            original_key = f"{original_key}|{quest_formid}"

        return cls(
            id=id_value,
            key=id_value,  # 将原来的id值复制到key
            original=getattr(ps, "string", "") or "",
            translation="",
            stage=STAGE_UNTRANSLATED,
            context=original_key,  # 原来的key值移动到context
            string_id=getattr(ps, "string_id", None),  # 传递 string_id
            form_id_with_plugin=full_form_id,  # 保存完整的 form_id
            # DSD 兼容字段
            dsd_type=getattr(ps, "type", "") or "",  # "NPC_ FULL" 格式
            dsd_index=getattr(ps, "index", 1) or 1,  # 原始索引，默认为 1
            editor_id=getattr(ps, "editor_id", "") or "",  # 原始 editor_id
            metadata=build_plugin_metadata(getattr(ps, "context", None), source_order),
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

        should_update = entry.stage == 0 and not entry.translation and bool(xt.dest)

        if not should_update:
            return entry

        # ---------- 4. 返回更新后的新实例 ----------

        return replace(entry, translation=xt.dest, stage=STAGE_TRANSLATED)

    @classmethod
    def try_update_from_sst(
        cls,
        entry: "TranslationEntry",
        sst: "SST_Entry",
    ) -> "TranslationEntry | None":
        """尝试用 SST_Entry 更新已有的 TranslationEntry。

        匹配策略: form_id + index（SST 有 form_id，比 XT 的 edid+index 更精确）。
        - 不匹配则返回 None
        - 匹配但不满足更新条件则返回原 entry
        - 匹配且满足条件则返回更新后的新 entry
        """

        # ---------- 1. 从 ESP ID 解析 form_id 和 index ----------

        # TranslationEntry.id 形如 "edid:form_id|index~TYPE:FIELD"
        # 或不含 ~ 后缀（如 SST 自身创建的条目）
        after_colon = entry.id.split(":", 1)[1]  # "form_id|index~TYPE"
        form_id_hex, _, rest = after_colon.partition("|")  # form_id, "|", "index~TYPE"
        index_str = rest.split("~")[0]
        entry_index = int(index_str) if index_str else None

        # ---------- 2. form_id 匹配 ----------

        if f"{sst.form_id:08X}" != form_id_hex.upper():
            return None

        # ---------- 3. index 匹配 ----------

        if entry_index is not None and entry_index != sst.index:
            return None

        # ---------- 4. 判断是否满足更新条件 ----------

        should_update = entry.stage == STAGE_UNTRANSLATED and not entry.translation and bool(sst.translated_text)

        if not should_update:
            return entry

        # ---------- 5. 返回更新后的新实例 ----------

        return replace(entry, translation=sst.translated_text, stage=STAGE_TRANSLATED)

    def to_dict(self) -> dict[str, Any]:
        """
        将 TranslationEntry 转为可序列化 dict。
        """
        return {
            "schema_version": self.SCHEMA_VERSION,
            "id": self.id,
            "key": self.key,
            "entry_key": self.identity.to_dict(),
            "external_refs": [reference.to_dict() for reference in self.external_refs],
            "revision": self.revision.value,
            "provenance": [item.to_dict() for item in self.provenance],
            "metadata": dict(self.metadata),
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
        entry_key_data = data.get("entry_key")
        if entry_key_data is not None:
            if not isinstance(entry_key_data, dict):
                raise TypeError("entry_key must be an object")
            entry_key = EntryKey.from_dict(entry_key_data)
        else:
            legacy_id = str(data.get("id", ""))
            legacy_key = str(data.get("key") or legacy_id)
            entry_key = EntryKey(SourceNamespace.legacy(), legacy_key)
        refs = tuple(ExternalEntryRef.from_dict(item) for item in data.get("external_refs", ()))
        provenance = tuple(Provenance.from_dict(item) for item in data.get("provenance", ()))
        metadata = data.get("metadata") or {}
        return cls(
            id=str(data.get("id", entry_key.local_key)),
            key=entry_key.local_key,
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
            entry_key=entry_key,
            external_refs=refs,
            revision=EntryRevision(data.get("revision", 0)),
            provenance=provenance,
            metadata=tuple(sorted(metadata.items())),
        )

    # ==================== DSD 格式转换 ====================

    # DSD 索引类型集合（需要 index 字段的类型）
    DSD_INDEX_TYPES = frozenset({"INFO NAM1", "QUST NNAM", "MESG ITXT", "PERK EPF2", "PERK EPFD"})

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
            stage=STAGE_TRANSLATED if string else STAGE_UNTRANSLATED,
            context=type_colon,
            form_id_with_plugin=form_id,
            dsd_type=type_str,
            dsd_index=index,
            editor_id=editor_id,
        )
