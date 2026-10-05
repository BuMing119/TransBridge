import pytest

from transbridge.ai_translator.noun_extractor import NounExtractor


class _Client:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.max_tokens: list[int] = []
        self.cancel_calls = 0

    def chat(self, messages, max_tokens):
        self.max_tokens.append(max_tokens)
        if self.error is not None:
            raise self.error
        return "fixture-response"

    def cancel(self) -> None:
        self.cancel_calls += 1


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


def test_extract_does_not_apply_a_client_side_output_token_limit() -> None:
    client = _Client()
    extractor = NounExtractor(client, _Builder([]))

    extractor.extract([{"original": "Riverwood", "translation": "溪木镇"}])

    assert client.max_tokens == [0]


def test_extract_forwards_configured_positive_output_limit_for_anthropic() -> None:
    client = _Client()
    extractor = NounExtractor(client, _Builder([]), max_output_tokens=2048)

    extractor.extract([{"original": "Riverwood", "translation": "溪木镇"}])

    assert client.max_tokens == [2048]


def test_extract_can_propagate_llm_failure_for_batch_orchestration() -> None:
    extractor = NounExtractor(_Client(error=RuntimeError("unauthorized")), _Builder([]))

    with pytest.raises(RuntimeError, match="unauthorized"):
        extractor.extract(
            [{"original": "Riverwood", "translation": "溪木镇"}],
            raise_on_error=True,
        )


def test_cancel_is_forwarded_to_the_provider_client() -> None:
    client = _Client()
    extractor = NounExtractor(client, _Builder([]))

    extractor.cancel()

    assert client.cancel_calls == 1


@pytest.mark.parametrize("text", ["...", "……", " ", "<br>", "{name}", "${name}", "%s", "[pagebreak]"])
def test_extract_rejects_nonlexical_candidates_even_when_present_in_both_texts(text):
    extractor = NounExtractor(_Client(), _Builder([{"term": text, "translation": text}]))
    assert extractor.extract([{"original": text, "translation": text}]) == []


def test_extract_rejects_word_fragments_on_either_side_and_text_inside_tags():
    extractor = NounExtractor(
        _Client(),
        _Builder([
            {"term": "Far", "translation": "法尔"},
            {"term": "法尔", "translation": "Far"},
            {"term": "name", "translation": "name"},
            {"term": "font", "translation": "font"},
        ]),
    )
    pairs = [
        {"original": "Farengar", "translation": "法尔加"},
        {"original": "法尔加", "translation": "Farengar"},
        {"original": "{name}", "translation": "{name}"},
        {"original": "<font>", "translation": "<font>"},
    ]
    assert extractor.extract(pairs) == []


def test_extract_preserves_names_abbreviations_cjk_and_existing_ambiguous_mapping():
    items = [
        {"term": "Eye", "translation": "马格努斯之眼"},
        {"term": "US", "translation": "US"},
        {"term": "D'Artagnan", "translation": "达达尼昂"},
        {"term": "巨龙", "translation": "Dragon"},
    ]
    extractor = NounExtractor(_Client(), _Builder(items))
    pairs = [
        {"original": "Eye of Magnus", "translation": "马格努斯之眼"},
        {"original": "US,", "translation": "US!"},
        {"original": "D'Artagnan's sword", "translation": "达达尼昂之剑"},
        {"original": "巨龙之剑", "translation": "Dragon sword"},
    ]
    assert [(row.term, row.translation) for row in extractor.extract(pairs)] == [
        (row["term"], row["translation"]) for row in items
    ]
