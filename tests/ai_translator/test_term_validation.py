import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.term_database import DynamicTermDatabase, TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry, dump_terms_json
from transbridge.ai_translator.term_validation import valid_term_pair
from transbridge.config.llm import LLMConfig


@pytest.mark.parametrize(
    "text", ["", " \n", "...", "！？", "___", "<font face='$Font'>", "{0}", "%02d", "${name}", r"\n"]
)
def test_syntax_and_punctuation_are_not_term_values(text):
    assert not valid_term_pair(text, "Name")
    assert not valid_term_pair("Name", text)


@pytest.mark.parametrize("text", ["US", "C++", "R2-D2", "D'Artagnan", "八圣灵", "Eye", "Far", "123"])
def test_real_names_and_unchanged_values_remain_valid(text):
    assert valid_term_pair(text, text)


def test_dynamic_admission_and_old_record_matching_do_not_rewrite_old_data(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    db = DynamicTermDatabase("test.esp")
    path = Path(db._path)
    old = [TermEntry("...", "...", "auto_dialogue"), TermEntry("Eye", "马格努斯之眼", "existing_text")]
    dump_terms_json(path, old)
    before = path.read_bytes()
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    manager = TermDatabaseManager(config, "test.esp")
    manager.load_all()
    assert path.read_bytes() == before
    assert manager.exact_match(["...", "Eye"]) == {"Eye": "马格努斯之眼"}
    assert manager.match_terms(["... eye"]) == {"Eye": "马格努斯之眼"}
    assert manager.match_terms_for_entry(SimpleNamespace(original="... eye")) == {"Eye": "马格努斯之眼"}
    assert path.read_bytes() == before
    db.load()
    db.add_many_and_save([
        ("{name}", "{name}", "auto_name", ""),
        ("Name", "...", "auto_dialogue", ""),
        ("US", "US", "auto_dialogue", ""),
    ])
    assert [(row.term, row.translation) for row in db.as_list()] == [
        ("...", "..."),
        ("Eye", "马格努斯之眼"),
        ("US", "US"),
    ]
    assert json.loads(path.read_text(encoding="utf-8"))


def test_aliases_semantic_cache_and_inflight_cannot_reintroduce_invalid_terms():
    manager = object.__new__(TermDatabaseManager)
    manager._effective_terms = lambda: [TermEntry("Riverwood", "溪木镇", "manual", variants=["...", "{name}"])]
    manager._retrieval_enabled = True
    manager._vector_lock = threading.RLock()
    manager._snapshot_identity = lambda _context: "legacy-global"
    rows = [SimpleNamespace(term="...", translation="..."), SimpleNamespace(term="Eye", translation="马格努斯之眼")]
    manager._vector_index = SimpleNamespace(
        available=True,
        search_batch=lambda *a, **kw: {"text": rows},
        search_hybrid_batch=lambda *a, **kw: {"text": rows},
    )
    assert manager.exact_match(["...", "{name}"]) == {}
    assert manager.match_terms(["... {name}"]) == {}
    assert manager.semantic_match(["text"]) == {"Eye": "马格努斯之眼"}
    for contextual in (False, True):
        manager._uses_project_context = lambda: contextual
        manager.lookup_context_for_entry = lambda _entry: None
        result = manager.match_terms_scoped(
            [SimpleNamespace(key="id", original="text")],
            in_flight_terms={"...": "...", "{name}": "Name"},
        )
        assert result.flat_terms == {"Eye": "马格努斯之眼"}
        assert result.terms_by_entry == {"id": {"Eye": "马格努斯之眼"}}
