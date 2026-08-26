from transbridge.ai_translator.noun_extractor import NounExtractor


class _Client:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def chat(self, messages, max_tokens):
        if self.error is not None:
            raise self.error
        return "fixture-response"


class _Builder:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def build_extraction_prompt(self, pairs):
        return [{"role": "user", "content": "fixture"}]

    def parse_extraction_response(self, response):
        return list(self.items)


def test_extract_keeps_only_exact_subsegments_from_the_same_pair() -> None:
    builder = _Builder([
        {"term": "Delphine", "translation": "戴尔芬"},
        {"term": "Delphine", "translation": "伊斯本"},
        {"term": "River wood", "translation": "溪木镇"},
        {"term": "Delphine", "translation": "戴尔芬"},
    ])
    extractor = NounExtractor(_Client(), builder)
    pairs = [
        {"original": "Meet Delphine in Riverwood.", "translation": "在溪木镇与戴尔芬会面。"},
        {"original": "Ask Esbern.", "translation": "询问伊斯本。"},
    ]

    terms = extractor.extract(pairs)

    assert [(term.term, term.translation, term.source) for term in terms] == [
        ("Delphine", "戴尔芬", "auto_dialogue"),
    ]


def test_extract_degrades_to_empty_when_llm_call_fails() -> None:
    extractor = NounExtractor(_Client(error=RuntimeError("offline")), _Builder([]))

    assert extractor.extract([{"original": "Riverwood", "translation": "溪木镇"}]) == []
