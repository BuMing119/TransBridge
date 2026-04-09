"""
术语库管理模块。

提供：
- DynamicTermDatabase：按 ESP stem 绑定，持久化到 data/ai_translator/{stem}/{stem}_terms.json
- TermDatabaseManager：加载四来源（dynamic/paratranz/json/excel），按优先级合并，支持缓存
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.paratranz.config_manager import LLMConfig
    from src.transbridge.converter.translation_entry import TranslationEntry

logger = logging.getLogger(__name__)

# 匹配术语开头的英文冠词（The / A / An），用于冠词规范化匹配
_ARTICLE_RE = re.compile(r'^(?:the|a|an)\s+', re.IGNORECASE)

# 缓存文件名
CACHE_FILES = {
    "paratranz": "paratranz_terms.json",
    "json": "json_terms.json",
    "excel": "excel_terms.json",
    "merged": "merged_terms.json",
}


@dataclass
class TermEntry:
    term: str
    translation: str
    source: str          # auto_name | auto_dialogue | manual | paratranz | json | excel
    context: str = ""
    created_at: str = ""
    case_sensitive: bool = False  # 仅 paratranz 来源可能为 True
    variants: list[str] = field(default_factory=list)  # 术语变体列表（单复数、缩写等）


# ─────────────────────────── DynamicTermDatabase ─────────────────────────────

class DynamicTermDatabase:
    """按 ESP stem 绑定，持久化到 data/ai_translator/{stem}/{stem}_terms.json。"""

    def __init__(self, esp_path: str):
        stem = os.path.splitext(os.path.basename(esp_path))[0]
        from src.transbridge.paratranz.config_manager import LLMConfig
        ai_dir = LLMConfig.get_ai_translator_dir(stem)
        self._path = os.path.join(ai_dir, f"{stem}_terms.json")
        self._entries: list[TermEntry] = []
        self._lock = threading.Lock()

    def load(self) -> None:
        if not os.path.exists(self._path):
            self._entries = []
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            self._entries = [TermEntry(**item) for item in raw]
        except Exception:
            self._entries = []

    def save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump([asdict(e) for e in self._entries], f, ensure_ascii=False, indent=2)

    def add(self, term: str, translation: str, source: str, context: str = "") -> None:
        for e in self._entries:
            if e.term == term:
                if e.source == "manual":
                    return  # 手动条目不被自动翻译覆盖
                e.translation = translation
                e.source = source
                e.context = context
                return
        self._entries.append(TermEntry(
            term=term,
            translation=translation,
            source=source,
            context=context,
            created_at=datetime.now().isoformat(timespec="seconds"),
        ))

    def add_many_and_save(self, terms: list[tuple[str, str, str, str]]) -> None:
        """原子性批量写入并保存，加锁保证并发安全。terms: [(term, translation, source, context), ...]"""
        with self._lock:
            for term, translation, source, context in terms:
                self.add(term, translation, source, context)
            self.save()

    def as_list(self) -> list[TermEntry]:
        return list(self._entries)


# ─────────────────────────── TermDatabaseManager ─────────────────────────────

class TermDatabaseManager:
    """
    加载四来源术语，按 priority 顺序合并（后加载的优先级高的覆盖低的），
    返回统一术语列表。

    支持向量语义检索：
    - 首次加载时自动构建 FAISS 索引
    - 术语库变化时可重建索引
    - 通过 match_terms_enhanced 启用两阶段召回
    """

    def __init__(
        self,
        config: "LLMConfig",
        esp_path: str,
        paratranz_client=None,
        project_id: int | None = None,
    ):
        self._config = config
        self._esp_path = esp_path
        self._paratranz_client = paratranz_client
        self._project_id = project_id
        self._dynamic_db = DynamicTermDatabase(esp_path)
        self._dynamic_db.load()
        self._merged_terms: list[TermEntry] = []  # 缓存合并后的术语列表
        self._load_log: list[tuple[str, int, str | None]] = []  # (source, count, error)
        self._vector_index = None  # 延迟初始化

        # 缓存目录：data/ai_translator/{stem}/cache/
        from src.transbridge.paratranz.config_manager import LLMConfig
        stem = os.path.splitext(os.path.basename(esp_path))[0]
        self._cache_dir = os.path.join(LLMConfig.get_ai_translator_dir(stem), "cache")
        os.makedirs(self._cache_dir, exist_ok=True)

    def get_dynamic_db(self) -> DynamicTermDatabase:
        return self._dynamic_db

    def load_all(self) -> dict[str, str]:
        """按 term_priority 顺序加载并合并，优先级从低到高（后者覆盖前者）。返回 {term: translation}。"""
        self._merged_terms = self._load_all_with_metadata()

        # 初始化向量索引
        self._init_vector_index()

        return {e.term: e.translation for e in self._merged_terms}

    def _get_cache_path(self, source: str) -> str:
        """获取指定来源的缓存文件路径。"""
        filename = CACHE_FILES.get(source, f"{source}_terms.json")
        return os.path.join(self._cache_dir, filename)

    def _save_source_cache(self, source: str, entries: list[TermEntry]) -> None:
        """保存单个来源的术语缓存。"""
        cache_path = self._get_cache_path(source)
        data = {
            "cached_at": datetime.now().isoformat(),
            "count": len(entries),
            "entries": [asdict(e) for e in entries],
        }
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存 {source} 术语缓存失败: {e}")

    def _load_source_cache(self, source: str) -> list[TermEntry] | None:
        """从缓存加载单个来源的术语，如果缓存不存在或无效返回 None。"""
        cache_path = self._get_cache_path(source)
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            entries = [TermEntry(**item) for item in data.get("entries", [])]
            logger.debug(f"从缓存加载 {source} 术语: {len(entries)} 条")
            return entries
        except Exception as e:
            logger.warning(f"加载 {source} 术语缓存失败: {e}")
            return None

    def save_merged_cache(self, entries: list[TermEntry]) -> None:
        """保存合并后的术语缓存，供外部工具（如 ConsistencyChecker）直接读取。"""
        cache_path = self._get_cache_path("merged")
        data = {
            "cached_at": datetime.now().isoformat(),
            "count": len(entries),
            "sources": self._config.term_priority if self._config else [],
            "entries": [asdict(e) for e in entries],
        }
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"合并术语库已缓存: {cache_path} ({len(entries)} 条)")
        except Exception as e:
            logger.warning(f"保存合并术语缓存失败: {e}")

    @staticmethod
    def load_merged_cache(esp_path: str) -> list[TermEntry]:
        """
        从硬盘直接加载合并后的术语缓存，无需创建 TermDatabaseManager 实例。

        供 ConsistencyChecker 等后处理工具使用。

        Args:
            esp_path: ESP 文件路径，用于定位缓存目录

        Returns:
            TermEntry 列表，缓存不存在或无效时返回空列表
        """
        from src.transbridge.paratranz.config_manager import LLMConfig
        stem = os.path.splitext(os.path.basename(esp_path))[0]
        cache_path = os.path.join(LLMConfig.get_ai_translator_dir(stem), "cache", CACHE_FILES["merged"])

        if not os.path.exists(cache_path):
            logger.debug(f"合并术语缓存不存在: {cache_path}")
            return []

        try:
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
            entries = [TermEntry(**item) for item in data.get("entries", [])]
            logger.info(f"从缓存加载合并术语库: {len(entries)} 条")
            return entries
        except Exception as e:
            logger.warning(f"加载合并术语缓存失败: {e}")
            return []

    def _load_all_with_metadata(self) -> list[TermEntry]:
        """加载并合并术语，保留 case_sensitive 等元数据。"""
        loaders = {
            "dynamic": self._load_dynamic,
            "paratranz": self._load_paratranz,
            "json": self._load_json,
            "excel": self._load_excel,
        }
        term_map: dict[str, TermEntry] = {}
        # 低优先级先加载，高优先级后加载覆盖
        for source in reversed(self._config.term_priority):
            loader = loaders.get(source)
            if loader:
                try:
                    # 先从源加载
                    entries = loader()
                    # 保存该来源的缓存
                    self._save_source_cache(source, entries)
                    for entry in entries:
                        term_map[entry.term] = entry
                    self._load_log.append((source, len(entries), None))
                except Exception as e:
                    # 源加载失败时尝试从缓存恢复
                    cached = self._load_source_cache(source)
                    if cached is not None:
                        for entry in cached:
                            term_map[entry.term] = entry
                        self._load_log.append((source, len(cached), f"from cache (source failed: {e})"))
                    else:
                        self._load_log.append((source, 0, str(e)))

        merged = list(term_map.values())
        # 保存合并后的缓存
        self.save_merged_cache(merged)
        return merged

    def get_load_log(self) -> list[tuple[str, int, str | None]]:
        """返回各来源加载结果：[(source, count, error_or_None), ...]。"""
        return list(self._load_log)

    def has_term(self, term: str) -> bool:
        """检查 term 是否已存在于任意来源（大小写不敏感）。"""
        if not self._merged_terms:
            self._merged_terms = self._load_all_with_metadata()
        term_lower = term.lower()
        for entry in self._merged_terms:
            if entry.term.lower() == term_lower:
                return True
        # 同时检查翻译过程中动态追加的新条目（_merged_terms 仅缓存初始状态）
        for entry in self._dynamic_db.as_list():
            if entry.term.lower() == term_lower:
                return True
        return False

    def _effective_terms(self) -> list[TermEntry]:
        """返回合并后的术语列表，并补充翻译过程中动态追加的新条目。"""
        if not self._merged_terms:
            self._merged_terms = self._load_all_with_metadata()
        # 把 _dynamic_db 中在 _merged_terms 之后新增的条目追加进来
        merged_terms_set = {e.term.lower() for e in self._merged_terms}
        extra = [e for e in self._dynamic_db.as_list() if e.term.lower() not in merged_terms_set]
        return self._merged_terms + extra

    def _get_term_matcher_map(self) -> dict[str, tuple[str, str, bool]]:
        """
        构建术语匹配映射表。

        返回: {匹配键: (主术语, 译文, case_sensitive)}
        匹配键包括：主术语本身 + 所有变体
        """
        matcher_map: dict[str, tuple[str, str, bool]] = {}
        for entry in self._effective_terms():
            # 主术语
            matcher_map[entry.term] = (entry.term, entry.translation, entry.case_sensitive)
            # 变体映射到主术语的译文
            for variant in entry.variants:
                if variant and variant not in matcher_map:
                    matcher_map[variant] = (entry.term, entry.translation, entry.case_sensitive)
        return matcher_map

    # ─────────────────────────── 向量索引 ─────────────────────────────

    def _init_vector_index(self) -> None:
        """初始化向量索引（延迟构建，失败时降级）。"""
        try:
            from .term_vector_index import TermVectorIndex
            from .embedding_client import create_embedding_client

            # 获取配置参数
            threshold = getattr(self._config, 'semantic_similarity_threshold', 0.8)
            top_k = getattr(self._config, 'semantic_top_k', 5)

            # 创建 EmbeddingClient
            embedding_client = create_embedding_client(self._config)

            if not embedding_client.available:
                self._load_log.append(("vector_index", 0, embedding_client.error_message))
                self._vector_index = None
                logger.warning(f"Vector index disabled: {embedding_client.error_message}")
                return

            self._vector_index = TermVectorIndex(
                self._esp_path,
                embedding_client=embedding_client,
                similarity_threshold=threshold,
                top_k_per_entry=top_k,
            )

            success = self._vector_index.build_index(self._merged_terms)
            if success:
                self._load_log.append(("vector_index", len(self._merged_terms), None))
            else:
                self._load_log.append(("vector_index", 0, self._vector_index.init_error))
                logger.warning(f"Vector index init failed: {self._vector_index.init_error}")

        except ImportError as e:
            self._load_log.append(("vector_index", 0, f"Missing dependency: {e}"))
            self._vector_index = None
            logger.info("Vector index disabled: faiss not installed")

    def rebuild_vector_index(self) -> bool:
        """手动重建向量索引（术语库更新后调用）。"""
        if self._vector_index is None:
            return False
        return self._vector_index.build_index(self._effective_terms(), force=True)

    def semantic_match(
        self,
        text_batch: list[str],
        top_k: int = 5,
    ) -> dict[str, str]:
        """
        语义召回术语。

        对每条原文进行语义检索，返回相似度超过阈值的术语。
        仅在向量索引可用时工作，否则返回空 dict。

        Args:
            text_batch: 原文列表
            top_k: 每条原文召回的候选数

        Returns:
            {term: translation} 合并后的术语表
        """
        if self._vector_index is None or not self._vector_index.available:
            return {}

        # 批量检索
        batch_results = self._vector_index.search_batch(text_batch, top_k=top_k)

        # 合并去重
        matched: dict[str, str] = {}
        for results in batch_results.values():
            for r in results:
                if r.term not in matched:
                    matched[r.term] = r.translation

        return matched

    def match_terms_enhanced(
        self,
        entries: list["TranslationEntry"],
        enable_semantic: bool = True,
        max_terms: int = 100,
        in_flight_terms: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        增强版术语匹配：两阶段召回策略。

        阶段1：子串扫描 - 找"明确出现"的术语
        阶段2：语义召回 - 为未命中原文补充"语义相关"的术语

        Args:
            entries: 翻译条目列表（需要 original 字段）
            enable_semantic: 是否启用语义召回
            max_terms: 术语表硬上限（防止 token 爆炸）
            in_flight_terms: 并发批次间实时共享的术语缓存（来自流式翻译）

        Returns:
            {term: translation} 合并后的术语表
        """
        originals = [e.original for e in entries]
        originals_lower = [o.lower() for o in originals]

        # 阶段1：精确匹配 + 子串扫描（分优先级）
        exact_matched = self.exact_match(originals)
        substring_matched = self.match_terms(originals)

        # 按优先级分类：精确全等 > 正向子串 > 反向匹配 > in-flight > 语义
        # 优先级分数：越小越优先
        priority: dict[str, int] = {}
        for term in exact_matched:
            priority[term] = 0  # 最高优先级
        for term in substring_matched:
            if term not in priority:
                tl = term.lower()
                # 判断是正向子串还是反向匹配
                is_forward = any(tl in o for o in originals_lower)
                priority[term] = 1 if is_forward else 2

        # 合并基础匹配
        matched = {**exact_matched, **substring_matched}

        # 合并 in-flight 术语（并发批次实时产生的术语）
        if in_flight_terms:
            for term, trans in in_flight_terms.items():
                if term not in matched:
                    matched[term] = trans
                    priority[term] = 2  # 与反向匹配同级

        # 阶段2：语义召回（仅对子串未命中的原文）
        semantic_terms: set[str] = set()
        if enable_semantic and self._vector_index and self._vector_index.available:
            # 找出没有子串命中的原文
            unmatched_entries = [
                e for e in entries
                if not any(
                    term.lower() in e.original.lower()
                    for term in substring_matched
                )
            ]

            # 对未命中原文做语义检索，补充高置信术语
            for entry in unmatched_entries[:10]:  # 最多处理 10 条
                results = self._vector_index.search(entry.original, top_k=3)
                for r in results:
                    if r.term not in matched:
                        matched[r.term] = r.translation
                        semantic_terms.add(r.term)
                        priority[r.term] = 3  # 语义召回优先级最低

        # 硬上限保护：按优先级排序后截断
        if len(matched) > max_terms:
            # 按优先级升序，同优先级按术语长度升序（短词更基础）
            sorted_terms = sorted(
                matched.keys(),
                key=lambda t: (priority.get(t, 99), len(t))
            )
            matched = {t: matched[t] for t in sorted_terms[:max_terms]}

        return matched

    def match_terms(self, text_batch: list[str]) -> dict[str, str]:
        """在 text_batch 的原文中扫描匹配的术语，返回 {term: translation}。

        匹配策略（按顺序，命中即止）：
        1. 正向子串：术语或其变体是原文的子串（原有逻辑）
        2. 冠词规范化：忽略术语开头的 The/A/An 后重试正向子串
        3. 反向前缀：原文是术语/变体的词边界前缀
           例："Black Briar" → 术语 "Black Briar Lodge → 黑棘据点"
        4. 反向后缀：原文是术语/变体的词边界后缀
           例："Meadery" → 术语 "Black Briar Meadery → 黑棘酿酒坊"

        匹配到变体时，返回主术语的译文。
        """
        combined_text = "\n".join(text_batch)
        combined_lower = combined_text.lower()
        # 反向匹配仅对长度 >= 4 的原文，避免过短词触发噪音
        originals_lower = [t.lower() for t in text_batch if len(t) >= 4]
        matched: dict[str, str] = {}

        matcher_map = self._get_term_matcher_map()

        for match_key, (main_term, translation, case_sensitive) in matcher_map.items():
            # 如果主术语已匹配，跳过变体检查
            if main_term in matched:
                continue

            mk_lower = match_key.lower()

            # 1. 正向子串
            if case_sensitive:
                if match_key in combined_text:
                    matched[main_term] = translation
                    continue
            else:
                if mk_lower in combined_lower:
                    matched[main_term] = translation
                    continue

            # 2. 冠词规范化：术语含冠词前缀，去掉后重试正向子串
            mk_no_art = _ARTICLE_RE.sub('', mk_lower)
            if mk_no_art != mk_lower and mk_no_art in combined_lower:
                matched[main_term] = translation
                continue

            # 3. 反向前缀：original 是 match_key 的词边界前缀
            for orig in originals_lower:
                if mk_lower.startswith(orig) and (len(mk_lower) == len(orig) or mk_lower[len(orig)] == ' '):
                    matched[main_term] = translation
                    break
            if main_term in matched:
                continue

            # 4. 反向后缀：original 是 match_key 的词边界后缀
            for orig in originals_lower:
                if mk_lower.endswith(orig) and (len(mk_lower) == len(orig) or mk_lower[-(len(orig) + 1)] == ' '):
                    matched[main_term] = translation
                    break

        return matched

    def exact_match(self, originals: list[str]) -> dict[str, str]:
        """对 originals 列表做精确全等匹配，返回 {original: translation}。
        区分大小写的术语要求精确相等；不区分大小写的术语忽略大小写。
        支持变体：如果原文精确匹配某个变体，返回主术语的译文。
        """
        # 构建两张查找表（O(n) 预处理，O(1) 查询）
        cs_map: dict[str, tuple[str, str]] = {}   # 区分大小写: exact term → (main_term, translation)
        ci_map: dict[str, tuple[str, str]] = {}   # 不区分大小写: lower term → (main_term, translation)
        for entry in self._effective_terms():
            # 主术语
            if entry.case_sensitive:
                cs_map[entry.term] = (entry.term, entry.translation)
            else:
                ci_map[entry.term.lower()] = (entry.term, entry.translation)
            # 变体
            for variant in entry.variants:
                if not variant:
                    continue
                if entry.case_sensitive:
                    cs_map[variant] = (entry.term, entry.translation)
                else:
                    ci_map[variant.lower()] = (entry.term, entry.translation)

        result: dict[str, str] = {}
        for original in originals:
            if original in cs_map:
                main_term, trans = cs_map[original]
                result[main_term] = trans
            elif original.lower() in ci_map:
                main_term, trans = ci_map[original.lower()]
                result[main_term] = trans
        return result

    def _load_dynamic(self) -> list[TermEntry]:
        return self._dynamic_db.as_list()

    def _load_paratranz(self) -> list[TermEntry]:
        if not self._paratranz_client or not self._project_id:
            return []
        results = []
        page = 1
        while True:
            data = self._paratranz_client.list_terms(self._project_id, page=page, page_size=100)
            items = data.get("results", []) if isinstance(data, dict) else []
            if not items:
                break
            for item in items:
                term = item.get("term", "")
                translation = item.get("translation", "")
                if term and translation:
                    results.append(TermEntry(
                        term=term,
                        translation=translation,
                        source="paratranz",
                        case_sensitive=bool(item.get("caseSensitive", False)),
                        variants=item.get("variants") or [],
                    ))
            if len(items) < 100:
                break
            page += 1
        return results

    def _load_json(self) -> list[TermEntry]:
        path = self._config.local_json_path
        if not path or not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        results = []
        if isinstance(raw, list):
            for item in raw:
                term = item.get("term", "") or item.get("original", "")
                translation = item.get("translation", "")
                if term and translation:
                    results.append(TermEntry(
                        term=term,
                        translation=translation,
                        source="json",
                        variants=item.get("variants") or [],
                    ))
        elif isinstance(raw, dict):
            for term, translation in raw.items():
                if term and translation:
                    results.append(TermEntry(term=str(term), translation=str(translation), source="json"))
        return results

    def _load_excel(self) -> list[TermEntry]:
        path = self._config.local_excel_path
        if not path or not os.path.exists(path):
            return []
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        col_orig = _col_letter_to_index(self._config.excel_original_col)
        col_trans = _col_letter_to_index(self._config.excel_translation_col)
        results = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            try:
                term = str(row[col_orig]) if row[col_orig] is not None else ""
                translation = str(row[col_trans]) if row[col_trans] is not None else ""
                if term and translation:
                    results.append(TermEntry(term=term, translation=translation, source="excel"))
            except (IndexError, TypeError):
                continue
        return results


def _col_letter_to_index(letter: str) -> int:
    """将列字母（A/B/AA 等）转换为 0 起始的列索引。"""
    letter = letter.upper().strip()
    idx = 0
    for ch in letter:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1
