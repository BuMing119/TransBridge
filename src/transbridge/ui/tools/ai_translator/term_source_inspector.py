"""Inspect configured term sources before starting a unified AI task."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class TermSourceInspector:
    @staticmethod
    def all_empty(config: object, esp_path: str | None) -> bool:
        if esp_path:
            from transbridge.ai_translator.term_database import DynamicTermDatabase

            database = DynamicTermDatabase(esp_path)
            database.load()
            if database.as_list():
                return False
        from transbridge.ai_translator.term_formats import load_terms_csv, load_terms_excel, load_terms_json

        sources = (
            ("JSON", getattr(config, "local_json_path", ""), load_terms_json),
            ("CSV", getattr(config, "local_csv_path", ""), load_terms_csv),
            (
                "Excel",
                getattr(config, "local_excel_path", ""),
                lambda path: load_terms_excel(
                    path,
                    original_column=getattr(config, "excel_original_col", "A") or "A",
                    translation_column=getattr(config, "excel_translation_col", "B") or "B",
                ),
            ),
        )
        for source_name, path, loader in sources:
            if not path or not os.path.exists(path):
                continue
            try:
                if loader(path):
                    return False
            except Exception as exc:
                logger.warning("检查%s术语来源失败 %s: %s", source_name, path, exc)
        return True

    @staticmethod
    def column_index(letter: str) -> int:
        result = 0
        for character in letter.upper().strip():
            result = result * 26 + ord(character) - ord("A") + 1
        return result - 1


__all__ = ["TermSourceInspector"]
