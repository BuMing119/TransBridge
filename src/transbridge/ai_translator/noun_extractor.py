"""
专有名词抽取器：对翻译完成的配对调用 LLM 进行术语提取。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transbridge.ai_translator.prompt_builder import PromptBuilder
    from transbridge.ai_translator.term_formats import TermEntry
    from transbridge.infra.llm_client import LLMClient


class NounExtractor:
    def __init__(self, llm_client: LLMClient, prompt_builder: PromptBuilder):
        self._client = llm_client
        self._builder = prompt_builder

    def extract(
        self,
        translated_pairs: list[dict],
        *,
        raise_on_error: bool = False,
    ) -> list[TermEntry]:
        """
        调用 LLM 抽取专有名词，返回 TermEntry 列表（source='auto_dialogue'）。

        translated_pairs: [{"original": ..., "translation": ...}]
        """
        if not translated_pairs:
            return []

        from transbridge.ai_translator.term_formats import TermEntry

        aligned_pairs = [
            (str(item.get("original") or ""), str(item.get("translation") or ""))
            for item in translated_pairs
            if isinstance(item, dict)
        ]

        try:
            messages = self._builder.build_extraction_prompt(translated_pairs)
            # ``0`` means that OpenAI-compatible providers use their own
            # completion limit instead of the former hard-coded 1000 tokens.
            response = self._client.chat(messages, max_tokens=0)
            raw_items = self._builder.parse_extraction_response(response)
        except Exception:
            if raise_on_error:
                raise
            return []

        results: list[TermEntry] = []
        seen: set[tuple[str, str]] = set()
        for item in raw_items:
            term = str(item.get("term") or "").strip()
            translation = str(item.get("translation") or "").strip()
            key = (term, translation)
            if (
                term
                and translation
                and key not in seen
                and any(term in original and translation in translated for original, translated in aligned_pairs)
            ):
                seen.add(key)
                results.append(
                    TermEntry(
                        term=term,
                        translation=translation,
                        source="auto_dialogue",
                    )
                )
        return results

    def cancel(self) -> None:
        """Interrupt provider calls owned by the current extraction run."""

        cancel = getattr(self._client, "cancel", None)
        if callable(cancel):
            cancel()
