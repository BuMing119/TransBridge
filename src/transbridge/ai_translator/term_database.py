"""
术语库管理模块。

提供：
- DynamicTermDatabase：按 ESP stem 绑定，持久化到 data/{stem}_terms.json
- TermDatabaseManager：加载四来源（dynamic/paratranz/json/excel），按优先级合并
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.paratranz.config_manager import LLMConfig


@dataclass
class TermEntry:
    term: str
    translation: str
    source: str          # auto_name | auto_dialogue | manual | paratranz | json | excel
    context: str = ""
    created_at: str = ""
    case_sensitive: bool = False  # 仅 paratranz 来源可能为 True


# ─────────────────────────── DynamicTermDatabase ─────────────────────────────

class DynamicTermDatabase:
    """按 ESP stem 绑定，持久化到 data/{stem}_terms.json。"""

    def __init__(self, esp_path: str):
        stem = os.path.splitext(os.path.basename(esp_path))[0]
        from src.transbridge.paratranz.config_manager import ParatranzConfig
        data_dir = ParatranzConfig.get_data_dir()
        self._path = os.path.join(data_dir, f"{stem}_terms.json")
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

    def get_dynamic_db(self) -> DynamicTermDatabase:
        return self._dynamic_db

    def load_all(self) -> dict[str, str]:
        """按 term_priority 顺序加载并合并，优先级从低到高（后者覆盖前者）。返回 {term: translation}。"""
        self._merged_terms = self._load_all_with_metadata()
        return {e.term: e.translation for e in self._merged_terms}

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
                    for entry in loader():
                        term_map[entry.term] = entry
                except Exception:
                    pass
        return list(term_map.values())

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

    def match_terms(self, text_batch: list[str]) -> dict[str, str]:
        """在 text_batch 的原文中扫描匹配的术语，返回 {term: translation}。"""
        if not self._merged_terms:
            self._merged_terms = self._load_all_with_metadata()

        combined_text = "\n".join(text_batch)
        matched: dict[str, str] = {}

        for entry in self._merged_terms:
            if entry.case_sensitive:
                # 区分大小写匹配
                if entry.term in combined_text:
                    matched[entry.term] = entry.translation
            else:
                # 不区分大小写匹配
                if entry.term.lower() in combined_text.lower():
                    matched[entry.term] = entry.translation

        return matched

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
                    results.append(TermEntry(term=term, translation=translation, source="json"))
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
