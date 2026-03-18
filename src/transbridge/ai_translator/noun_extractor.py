"""
专有名词抽取器：对翻译完成的配对调用 LLM 进行术语提取。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.ai_translator.llm_client import LLMClient
    from src.transbridge.ai_translator.prompt_builder import PromptBuilder
    from src.transbridge.ai_translator.term_database import TermEntry


class NounExtractor:
    def __init__(self, llm_client: "LLMClient", prompt_builder: "PromptBuilder"):
        self._client = llm_client
        self._builder = prompt_builder

    def extract(
        self,
        translated_pairs: list[dict],
    ) -> list["TermEntry"]:
        """
        调用 LLM 抽取专有名词，返回 TermEntry 列表（source='auto_dialogue'）。

        translated_pairs: [{"original": ..., "translation": ...}]
        """
        if not translated_pairs:
            return []

        from src.transbridge.ai_translator.term_database import TermEntry

        messages = self._builder.build_extraction_prompt(translated_pairs)
        try:
            response = self._client.chat(messages, max_tokens=1000)
            raw_items = self._builder.parse_extraction_response(response)
        except Exception:
            return []

        results: list[TermEntry] = []
        for item in raw_items:
            term = item.get("term", "").strip()
            translation = item.get("translation", "").strip()
            if term and translation:
                results.append(TermEntry(
                    term=term,
                    translation=translation,
                    source="auto_dialogue",
                ))
        return results
