"""翻译记忆管理器：多词典定位、组合查询、写入与持久化。

一文件一 mod（见 ADR-014 更新节 2026-08-14）：词典以 mod 文件为粒度，
定位键为 mod_file_id，scope 为单值属性标签。查询按「同名 mod → 其余 project
→ 其余 global」全查兜底，键索引优先，多词典命中收集候选后仲裁，conflicts 真正填充。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import threading

from transbridge.application.io.identity import Provenance
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.config.paths import get_data_dir
from transbridge.converter.translation_entry import _normalize_text
from transbridge.persistence._utils import atomic_write_json, validate_name
from transbridge.translation_memory.model import (
    SCOPE_GLOBAL,
    SCOPE_RANK,
    VALID_SCOPES,
    Dictionary,
    DictionaryEntry,
    entry_id,
)

# 词典文件后缀（内容为 JSON）
DICT_SUFFIX = ".tbdict"

# 规范化缓存：原文 -> 规范化文本（长文本替换/剥离只做一次）
_normalize_cache: dict[str, str] = {}
_normalize_lock = threading.Lock()


def _normalize(text: str) -> str:
    """规范化原文（带缓存 + 锁），复用 converter 的 _normalize_text 语义。"""
    with _normalize_lock:
        if text not in _normalize_cache:
            _normalize_cache[text] = _normalize_text(text)
            # 防缓存无限增长：超阈值清空
            if len(_normalize_cache) > 100_000:
                _normalize_cache.clear()
                _normalize_cache[text] = _normalize_text(text)
        return _normalize_cache[text]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class QueryContext:
    """查询上下文：决定激活集与兜底顺序。"""

    mod_file_id: str = ""  # 当前翻译的 mod 名（同名词典最高优先）


@dataclass
class QueryResult:
    """查询结果。"""

    translation: str | None = None
    matched_scope: str = ""  # 命中的 scope（project/global）
    matched_via: str = ""  # "key" | "text"
    match_status: str = ""  # "EXACT"（原文一致）| "STALE"（原文已变）| ""（文本命中）
    matched_mod: str = ""  # 命中的词典 mod_file_id
    conflicts: list[dict] = field(default_factory=list)  # 冲突候选（含译文/来源/胜者）


@dataclass
class ApplyResult:
    """套用结果统计。"""

    key_hits: int = 0  # 键索引命中条数
    text_hits: int = 0  # 文本索引命中条数
    misses: int = 0  # 未命中条数
    applied: int = 0  # 实际被填充译文的条数
    needs_review: list[str] = field(default_factory=list)  # 键命中但原文变化（STALE）的 entry_id
    conflicts: list[dict] = field(default_factory=list)  # 冲突候选（供 GUI 仲裁）


class TranslationMemoryManager:
    """管理多本词典：定位、查询、写入、持久化。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir: Path | None = base_dir
        self._dicts: dict[str, Dictionary] = {}  # mod_file_id -> Dictionary
        self._lock = threading.Lock()  # 保护 _dicts 与词典内容（命中计数、写入）

    # ------------------------------------------------------------------
    # 默认目录
    # ------------------------------------------------------------------

    def default_dir(self) -> Path:
        """默认词典目录：data/translation_memory/。"""
        return Path(get_data_dir()) / "translation_memory"

    # ------------------------------------------------------------------
    # 定位
    # ------------------------------------------------------------------

    def _key(self, mod_file_id: str) -> str:
        """计算定位键（mod_file_id，非空校验）。"""
        if not mod_file_id or not mod_file_id.strip():
            raise ValueError("mod_file_id 不能为空")
        return mod_file_id.strip()

    def _dict(self, mod_file_id: str) -> Dictionary:
        """定位或新建词典（单写者：由 _lock 保护）。"""
        key = self._key(mod_file_id)
        if key not in self._dicts:
            self._dicts[key] = Dictionary(mod_file_id=key)
        return self._dicts[key]

    @property
    def dictionaries(self) -> dict[str, Dictionary]:
        """mod_file_id -> Dictionary。"""
        return self._dicts

    def snapshot_dictionaries(self) -> tuple[Dictionary, ...]:
        """Return detached dictionaries for V2 read-only planning."""
        with self._lock:
            return tuple(Dictionary.from_dict(item.to_dict()) for item in self._dicts.values())

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add(
        self,
        complete_key: str,
        original: str,
        translation: str,
        mod_file_id: str = "",
        scope: str = SCOPE_GLOBAL,
        tags: list[str] | None = None,
        source_mod: str = "",
        form_id_with_plugin: str = "",
        overwrite: bool = False,
        *,
        source_locale: str = "",
        target_locale: str = "",
        stage: int = 0,
        provenance: tuple[Provenance, ...] = (),
        source_namespace: str = "",
        source_fingerprint: str = "",
        enabled: bool | None = None,
    ) -> None:
        """写入一条译文到词典（单表权威对象 + 双索引）。

        键索引用 complete_key，文本索引用规范化原文；两条索引指向同一 entry_id。
        """
        if not translation:
            return
        with self._lock:
            key = self._key(mod_file_id)
            d = self._dict(key)
            d.scope = scope  # 词典 scope 随写入同步（一本词典一个 scope）
            d.revision += 1
            nk = _normalize(original) if original else ""
            seed = nk or complete_key or original
            if not seed:
                return
            eid = entry_id(key, seed)
            now = _now()
            if eid in d.entries and not overwrite:
                entry = d.entries[eid]
                # 已存在：不覆盖正文，仅追加词典标签（去重）
                merged = set(entry.tags) | set(tags or [])
                entry.tags = sorted(merged)
            else:
                entry = DictionaryEntry(
                    translation=translation,
                    original=original,
                    source_mod=source_mod or key,
                    form_id_with_plugin=form_id_with_plugin or "",
                    imported_at=d.entries[eid].imported_at if eid in d.entries else now,
                    updated_at=now,
                    tags=sorted(set(tags or [])),
                    source_locale=source_locale,
                    target_locale=target_locale,
                    stage=stage,
                    provenance=provenance,
                    dictionary_id=d.dictionary_id or key,
                    dictionary_revision=d.revision,
                    source_namespace=source_namespace,
                    source_fingerprint=source_fingerprint,
                    enabled=(
                        bool(source_locale and target_locale and source_namespace and source_fingerprint)
                        if enabled is None
                        else enabled
                    ),
                )
                d.entries[eid] = entry
            # 双索引：只登记 entry_id + 初始 hits=0
            if complete_key:
                idx = d.key_index.setdefault(complete_key, {"entry_id": eid, "hits": 0})
                idx["entry_id"] = eid
            if nk:
                idx = d.text_index.setdefault(nk, {"entry_id": eid, "hits": 0})
                idx["entry_id"] = eid

    def set_scope(self, mod_file_id: str, scope: str) -> None:
        """切换词典 scope（单值覆盖）。"""
        if scope not in VALID_SCOPES:
            raise ValueError(f"非法 scope: {scope!r}（合法值: {sorted(VALID_SCOPES)}）")
        with self._lock:
            d = self._dict(mod_file_id)
            d.scope = scope

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def _hit(self, d: Dictionary, complete_key: str, original: str, via: str, status: str) -> QueryResult:
        """在单本词典内命中，返回 QueryResult（不计 conflicts）。"""
        nk = _normalize(original) if original else ""
        if via == "key":
            idx = d.key_index[complete_key]
            eid = idx.get("entry_id", "")
        else:
            idx = d.text_index[nk]
            eid = idx.get("entry_id", "")
        idx["hits"] = int(idx.get("hits", 0)) + 1
        entry = d.entries.get(eid)
        if entry is None:
            return QueryResult()
        return QueryResult(
            translation=entry.translation,
            matched_scope=d.scope,
            matched_via=via,
            match_status=status,
            matched_mod=d.mod_file_id,
        )

    def query(self, complete_key: str, original: str, context: QueryContext | None = None) -> QueryResult:
        """多词典全查兜底：同名 mod 优先（键命中即停），其余 project/global 收集候选仲裁。"""
        ctx = context or QueryContext()
        nk = _normalize(original) if original else ""

        with self._lock:
            # ① 同名 mod 词典：键命中即停（最可信）；文本命中也直接返回（不收集候选）
            same = self._dicts.get(ctx.mod_file_id)
            if same is not None:
                if complete_key and complete_key in same.key_index:
                    entry = same.entries.get(same.key_index[complete_key].get("entry_id", ""))
                    if entry is not None:
                        status = "EXACT" if _normalize(entry.original) == nk else "STALE"
                        return self._hit(same, complete_key, original, "key", status)
                if nk and nk in same.text_index:
                    return self._hit(same, None, original, "text", "")

            # ② 其余词典（按 scope 优先级 project > global）收集候选
            candidates: list[tuple[Dictionary, str, str, str]] = []  # (dict, key, via, status)
            rest = [d for mid, d in self._dicts.items() if mid != ctx.mod_file_id]
            rest.sort(key=lambda d: SCOPE_RANK.get(d.scope, 0), reverse=True)

            for d in rest:
                if complete_key and complete_key in d.key_index:
                    entry = d.entries.get(d.key_index[complete_key].get("entry_id", ""))
                    if entry is not None:
                        status = "EXACT" if _normalize(entry.original) == nk else "STALE"
                        candidates.append((d, complete_key, "key", status))
                elif nk and nk in d.text_index:
                    candidates.append((d, nk, "text", ""))

            if not candidates:
                return QueryResult()
            return self._arbitrate(candidates)

    def _arbitrate(self, candidates: list[tuple[Dictionary, str, str, str]]) -> QueryResult:
        """对候选按 scope 优先级 + hits 降序仲裁，填充 conflicts。"""

        def hits_of(c: tuple[Dictionary, str, str, str]) -> int:
            d, k, via, _ = c
            idx = d.key_index.get(k) if via == "key" else d.text_index.get(k)
            return int(idx.get("hits", 0)) if idx else 0

        def scope_rank_of(c: tuple[Dictionary, str, str, str]) -> int:
            return SCOPE_RANK.get(c[0].scope, 0)

        ordered = sorted(candidates, key=lambda c: (scope_rank_of(c), hits_of(c)), reverse=True)
        winner = ordered[0]
        d, k, via, status = winner
        idx = d.key_index.get(k) if via == "key" else d.text_index.get(k)
        if idx is not None:
            idx["hits"] = int(idx.get("hits", 0)) + 1
        entry = d.entries.get(idx.get("entry_id", "")) if idx else None
        if entry is None:
            return QueryResult()

        conflicts: list[dict] = []
        seen_trans = {entry.translation}
        for c in ordered[1:]:
            cd, ck, cvia, _ = c
            cidx = cd.key_index.get(ck) if cvia == "key" else cd.text_index.get(ck)
            ce = cd.entries.get(cidx.get("entry_id", "")) if cidx else None
            if ce is None:
                continue
            if ce.translation in seen_trans:
                continue
            seen_trans.add(ce.translation)
            conflicts.append({
                "translation": ce.translation,
                "mod_file_id": cd.mod_file_id,
                "scope": cd.scope,
                "wins": False,
            })
        # 胜者自身若与其他候选 wins 状态需明确：胜者 conf 标记 wins=True
        result = QueryResult(
            translation=entry.translation,
            matched_scope=d.scope,
            matched_via=via,
            match_status=status,
            matched_mod=d.mod_file_id,
            conflicts=conflicts,
        )
        return result

    # ------------------------------------------------------------------
    # 存为词典（从集合落地）
    # ------------------------------------------------------------------

    def save_from_collection(
        self,
        collection,
        mod_file_id: str = "",
        scope: str = SCOPE_GLOBAL,
        entry_ids: list[str] | None = None,
        tags: list[str] | None = None,
        *,
        source_locale: str = "",
        target_locale: str = "",
        source_namespace: str = "",
        source_fingerprint: str = "",
        provenance: tuple[Provenance, ...] = (),
    ) -> int:
        """将集合已译条目写入词典（来源 mod = mod_file_id）。返回新增 entry_id 数。

        排除 stage==9（锁定）/ stage==-1（隐藏）与空译文/空原文条目。
        """
        if collection is None:
            raise ValueError("collection 不能为 None")
        key = self._key(mod_file_id)
        if entry_ids is not None:
            targets = [collection.get(i) for i in entry_ids]
            targets = [e for e in targets if e is not None]
        else:
            targets = list(collection)

        before = set(self._dicts[key].entries.keys()) if key in self._dicts else set()

        for e in targets:
            if not e.translation or not e.original:
                continue
            if not DEFAULT_STAGE_POLICY.allows_tm_write(e.stage, e.translation, original=e.original):
                continue
            # 锁语义：e.key = TranslationEntry 唯一主索引（EditorID:FormID|index~context），
            # 即词典 key_index 的 complete_key，勿改用 e.id（id 非主索引，见 ADR-002）
            self.add(
                e.key,
                e.original,
                e.translation,
                mod_file_id=key,
                scope=scope,
                tags=tags,
                source_mod=key,
                form_id_with_plugin=getattr(e, "form_id_with_plugin", None) or "",
                source_locale=source_locale,
                target_locale=target_locale,
                stage=e.stage,
                provenance=provenance or tuple(getattr(e, "provenance", ())),
                source_namespace=source_namespace,
                source_fingerprint=source_fingerprint,
            )

        if key in self._dicts:
            after = set(self._dicts[key].entries.keys())
            return len(after - before)
        return 0

    # ------------------------------------------------------------------
    # 套用到集合
    # ------------------------------------------------------------------

    def apply_to_collection(
        self, collection, context: QueryContext | None = None, overwrite: bool = False
    ) -> ApplyResult:
        """按词典补集合空译文。返回统计（含 conflicts）。"""
        if collection is None:
            raise ValueError("collection 不能为 None")
        result = ApplyResult()
        for e in collection:
            if e.translation and not overwrite:
                continue
            if not DEFAULT_STAGE_POLICY.allows_tm_read(e.stage, e.translation, original=e.original):
                continue
            if not e.key and not e.original:
                continue
            # 锁语义：e.key 是主索引（=词典 key_index 的 complete_key），勿改用 e.id
            res = self.query(e.key, e.original, context)
            if res.translation:
                e.translation = res.translation
                result.applied += 1
                if res.matched_via == "key":
                    result.key_hits += 1
                    if res.match_status == "STALE":
                        result.needs_review.append(e.key)
                else:
                    result.text_hits += 1
                if res.conflicts:
                    for c in res.conflicts:
                        c["entry_id"] = e.key
                        result.conflicts.append(c)
            else:
                result.misses += 1
        return result

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _file_for(self, mod_file_id: str) -> str:
        return f"{validate_name(mod_file_id)}{DICT_SUFFIX}"

    def save(self, base_dir: Path | None = None) -> list[Path]:
        """持久化全部词典到 base_dir。返回写入的文件路径列表。"""
        target = base_dir or self._base_dir or self.default_dir()
        target.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        with self._lock:
            for key, d in self._dicts.items():
                fname = self._file_for(key)
                atomic_write_json(target / fname, d.to_dict())
                written.append(target / fname)
        return written

    def load(self, base_dir: Path | None = None) -> int:
        """从 base_dir 扫描并加载 .tbdict 词典文件。返回加载的词典数。"""
        target = base_dir or self._base_dir or self.default_dir()
        loaded: dict[str, Dictionary] = {}
        for f in sorted(target.glob(f"*{DICT_SUFFIX}")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                d = Dictionary.from_dict(data)
            except OSError as exc:
                raise RuntimeError(f"词典读取失败: {f}（{exc}）") from exc
            except Exception as exc:  # noqa: BLE001 - 损坏文件保留现场不静默吞
                corrupt = f.with_suffix(f".corrupt-{int(datetime.now().timestamp())}")
                f.replace(corrupt)
                raise RuntimeError(f"词典文件损坏，已保留现场: {corrupt}（{exc}）") from exc
            if d.mod_file_id in loaded:
                raise RuntimeError(f"词典 mod_file_id 重复: {d.mod_file_id}（{f}）")
            loaded[d.mod_file_id] = d
        with self._lock:
            self._dicts = loaded
        return len(loaded)

    def import_dict(self, src_path: str | Path, overwrite: bool = False) -> bool:
        """导入外部 .tbdict 词典，同步名校验。返回是否成功（同名且不覆盖返回 False）。"""
        import shutil

        src = Path(src_path)
        if src.suffix.lower() != DICT_SUFFIX:
            raise ValueError(f"仅支持 {DICT_SUFFIX} 词典文件")
        data = json.loads(src.read_text(encoding="utf-8"))
        d = Dictionary.from_dict(data)  # 损坏则抛
        target_dir = self._base_dir or self.default_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / self._file_for(d.mod_file_id)
        if target.exists() and not overwrite:
            return False
        shutil.copy2(src, target)
        with self._lock:
            self._dicts[d.mod_file_id] = d
        return True

    def export_dict(self, mod_file_id: str, dest_dir: str | Path) -> Path:
        """导出词典到目标目录，返回目标路径。"""
        import shutil

        key = self._key(mod_file_id)
        target_dir = Path(dest_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        src = (self._base_dir or self.default_dir()) / self._file_for(key)
        if not src.exists():
            # 内存中可能未落盘，先保存
            self.save()
            src = (self._base_dir or self.default_dir()) / self._file_for(key)
        dest = target_dir / self._file_for(key)
        shutil.copy2(src, dest)
        return dest

    def merge(self, other: TranslationMemoryManager) -> int:
        """合并另一个 manager 的全部词典。返回新增 entry_id 数。"""
        added = 0
        for key, od in other.dictionaries.items():
            before = set(self._dicts[key].entries.keys()) if key in self._dicts else set()
            d = self._dict(key)
            for eid, entry in od.entries.items():
                if eid not in d.entries:
                    d.entries[eid] = entry
            for ck, idx in od.key_index.items():
                d.key_index.setdefault(ck, dict(idx))
            for nk, idx in od.text_index.items():
                d.text_index.setdefault(nk, dict(idx))
            after = set(d.entries.keys())
            added += len(after - before)
        return added
