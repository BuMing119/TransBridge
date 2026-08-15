"""Shared pytest fixtures for TransBridge test suite.

Usage:
    # pytest-style: use fixtures
    def test_something(make_entry, mock_app_context): ...

    # unittest-style: import directly
    from tests.conftest import make_entry, make_test_collection, MockAppContext, MockSignal
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.transbridge.converter.translation_entry import TranslationEntry
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


# ── Plain Functions (usable from both pytest fixtures and unittest imports) ─


def make_entry(
    eid: str = "entry_000",
    original: str = "",
    translation: str = "",
    stage: int = 0,
    context: str = "NPC_:FULL",
) -> TranslationEntry:
    return TranslationEntry(
        id=eid, key=eid, original=original, translation=translation,
        stage=stage, context=context,
    )


def make_test_collection(n: int = 10) -> TranslationEntryCollection:
    """Create a collection with varied stages and contexts."""
    entries = []
    stage_map = [0, 1, 2, 3, 5]
    for i in range(n):
        s = stage_map[i % 5]
        ctx = "NPC_:FULL" if i % 3 != 0 else "INFO:NAM1"
        entries.append(make_entry(
            f"entry_{i:03d}",
            original=f"Original text {i}",
            translation=f"Translation text {i}" if s != 0 else "",
            stage=s,
            context=ctx,
        ))
    return TranslationEntryCollection(entries)


class MockSignal:
    """Minimal Qt signal mock."""
    def __init__(self):
        self.calls: list = []
    def emit(self, *args):
        self.calls.append(args)
    def connect(self, _fn):
        pass


class MockAppContext:
    """Comprehensive mock covering the full ViewModel surface.

    Supports: collection, active_slot, filter_state, labels, scope, selection,
    slots management, config, safe_mutate, signals, and paths.
    """

    def __init__(self, collection=None):
        self._filter_state = {
            "stage": [], "category": [], "label": [],
            "search_query": "", "search_field": "text",
        }
        self._label_library: dict[str, dict] = {}
        self._entry_labels: dict[str, set] = {}
        self._translation_scope: dict = {
            "stages": [], "labels": [], "categories": [], "action": "include",
        }
        self._selected_ids: set = set()

        self.filter_changed = MockSignal()
        self.label_data_changed = MockSignal()
        self.collection_changed = MockSignal()
        self.collection_changed_calls: list = []

        if collection is not None:
            from src.transbridge.ui.context import CollectionSlot
            slot = CollectionSlot(label="test", collection=collection)
            self._slots = {"test_key": slot}
            self._active_key = "test_key"
        else:
            self._slots = {}
            self._active_key = None

        self.esp_path = None
        self.eet_path = None
        self.xt_path = None
        self.active_project = None
        self.current_project = None
        self.paratranz_project_id = None
        self.active_variant = None
        self.workspace = None
        self._config = MagicMock()
        self._config.api_token = "test_token"

    # ── collection proxy ──
    @property
    def collection(self):
        slot = self.active_slot
        return slot.collection if slot else None

    # ── slots ──
    @property
    def active_slot(self):
        if self._active_key and self._active_key in self._slots:
            return self._slots[self._active_key]
        if self._collection_ref is not None:
            slot = SimpleNamespace()
            slot.collection = self._collection_ref
            return slot
        return None

    @property
    def slots(self) -> dict:
        return self._slots

    @property
    def active_key(self) -> str | None:
        return self._active_key

    # ── filter_state ──
    @property
    def filter_state(self) -> dict:
        return dict(self._filter_state)

    @filter_state.setter
    def filter_state(self, v: dict) -> None:
        self._filter_state = dict(v)
        self.filter_changed.emit(dict(self._filter_state))

    def set_filter(self, **kwargs) -> None:
        changed = False
        for k, v in kwargs.items():
            if k in self._filter_state and self._filter_state[k] != v:
                self._filter_state[k] = v
                changed = True
        if changed:
            self.filter_changed.emit(dict(self._filter_state))

    def clear_filters(self) -> None:
        self._filter_state = {"stage": [], "category": [], "label": [], "search_query": "", "search_field": "text"}
        self.filter_changed.emit(dict(self._filter_state))

    # ── labels ──
    @property
    def label_library(self) -> dict:
        return self._label_library

    @label_library.setter
    def label_library(self, v: dict) -> None:
        self._label_library = v
        self.label_data_changed.emit()

    @property
    def entry_labels(self) -> dict:
        return self._entry_labels

    @entry_labels.setter
    def entry_labels(self, v: dict) -> None:
        self._entry_labels = v
        self.label_data_changed.emit()

    # ── scope ──
    @property
    def translation_scope(self) -> dict:
        return dict(self._translation_scope)

    @translation_scope.setter
    def translation_scope(self, v: dict) -> None:
        stages = v.get("stages", [])
        if not isinstance(stages, list) or not all(isinstance(s, int) for s in stages):
            raise TypeError("stages must be list[int]")
        action = v.get("action", "include")
        if action not in ("include", "exclude", "only"):
            raise ValueError(f"invalid action: {action}")
        self._translation_scope = {
            "stages": list(stages), "labels": list(v.get("labels", [])),
            "categories": list(v.get("categories", [])), "action": action,
        }

    # ── selection ──
    @property
    def selected_ids(self) -> set:
        return self._selected_ids

    @selected_ids.setter
    def selected_ids(self, v: set) -> None:
        self._selected_ids = v

    def select_entries(self, entry_ids: list, action: str = "select") -> int:
        if action == "clear":
            self._selected_ids.clear()
        elif action == "select":
            self._selected_ids.update(entry_ids)
        elif action == "deselect":
            self._selected_ids.difference_update(entry_ids)
        return len(self._selected_ids)

    # ── mutation ──
    def safe_mutate(self, fn) -> None:
        fn()

    def notify_collection_modified(self) -> None:
        self.collection_changed_calls.append(True)

    # ── config ──
    @property
    def config(self):
        return self._config

    # ── back-compat for postprocess tests ──
    _collection_ref = None

    @classmethod
    def with_collection(cls, collection):
        """Factory for postprocess-style tests that only need collection access."""
        inst = cls(collection=None)
        inst._collection_ref = collection
        return inst


class MockToolSpec:
    """Minimal tool spec for guardrail/execute tests."""
    def __init__(self, name, permission, execute):
        self.name = name
        self.permission = permission
        self.parameters = {}
        self.execute = execute
        self.require_confirmation = False
        self.is_long_running = False


def make_llm_config(**overrides):
    """Create a mock LLMConfig with sensible defaults for all pp_* fields."""
    cfg = MagicMock()
    cfg.api_key = overrides.get("api_key", "")
    cfg.game_profile = overrides.get("game_profile", "skyrim_se")
    cfg.target_lang = overrides.get("target_lang", "zh_CN")
    cfg.pp_enable_consistency_check = overrides.get("pp_enable_consistency_check", True)
    cfg.pp_enable_format_validation = overrides.get("pp_enable_format_validation", True)
    cfg.pp_enable_quality_gate = overrides.get("pp_enable_quality_gate", True)
    cfg.pp_quality_gate_batch_size = overrides.get("pp_quality_gate_batch_size", 10)
    cfg.pp_enable_refinement = overrides.get("pp_enable_refinement", True)
    cfg.pp_refinement_batch_size = overrides.get("pp_refinement_batch_size", 5)
    cfg.pp_enable_polish = overrides.get("pp_enable_polish", False)
    cfg.pp_polish_scope = overrides.get("pp_polish_scope", "all")
    cfg.pp_polish_level = overrides.get("pp_polish_level", "moderate")
    cfg.pp_polish_batch_size = overrides.get("pp_polish_batch_size", 5)
    cfg.pp_enable_arbitration = overrides.get("pp_enable_arbitration", True)
    cfg.pp_strict_arbitration = overrides.get("pp_strict_arbitration", False)
    cfg.pp_arbitration_batch_size = overrides.get("pp_arbitration_batch_size", 10)
    cfg.term_priority = overrides.get("term_priority", ["dynamic"])
    cfg.local_json_path = overrides.get("local_json_path", "")
    cfg.local_excel_path = overrides.get("local_excel_path", "")
    cfg.enable_semantic_match = overrides.get("enable_semantic_match", False)
    cfg.semantic_similarity_threshold = overrides.get("semantic_similarity_threshold", 0.7)
    cfg.semantic_top_k = overrides.get("semantic_top_k", 5)
    cfg.max_terms_per_batch = overrides.get("max_terms_per_batch", 50)
    cfg.polish_preview_enabled = overrides.get("polish_preview_enabled", False)
    cfg.max_concurrent = overrides.get("max_concurrent", 3)
    cfg.llm_max_retries = overrides.get("llm_max_retries", 2)
    cfg.max_tokens_per_batch = overrides.get("max_tokens_per_batch", 2000)
    cfg.max_output_tokens = overrides.get("max_output_tokens", 0)
    cfg.excel_original_col = overrides.get("excel_original_col", "A")
    cfg.excel_translation_col = overrides.get("excel_translation_col", "B")
    cfg.enable_post_process = overrides.get("enable_post_process", True)
    cfg.guardrails_enable_admin_confirm = True
    cfg.guardrails_enable_input_validation = True
    cfg.guardrails_enable_output_validation = True
    cfg.guardrails_max_input_size = 102400
    cfg.guardrails_write_require_confirm = False
    cfg.mcp_enabled = False
    cfg.mcp_transport = "stdio"
    cfg.mcp_admin_tool_whitelist = ""
    cfg.mcp_write_tool_policy = "deny"
    cfg.mcp_auth_token = ""
    cfg.action_rules = []
    cfg.mixed_execution_order = "serial"
    cfg.embedding = MagicMock()
    cfg.embedding.mode = "disabled"
    cfg.provider = "openai_compatible"
    cfg.base_url = "https://api.openai.com/v1"
    cfg.model = "gpt-4o"
    cfg.save_to_file = MagicMock()
    cfg.load_from_file = MagicMock(return_value=cfg)
    from src.transbridge.config.llm import LLMConfig
    cfg.get_ai_translator_dir = LLMConfig.get_ai_translator_dir
    return cfg


# ── Pytest Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def entry_factory():
    """Fixture wrapping make_entry for pytest-style tests."""
    return make_entry


@pytest.fixture
def sample_collection():
    """Fixture: a pre-built 10-entry collection."""
    return make_test_collection(10)


@pytest.fixture
def mock_signal():
    """Fixture: fresh MockSignal instance."""
    return MockSignal()


@pytest.fixture
def mock_app_ctx():
    """Fixture: MockAppContext with no collection (caller adds collection as needed)."""
    return MockAppContext()


@pytest.fixture
def mock_app_ctx_with_collection():
    """Fixture: MockAppContext pre-loaded with a 10-entry collection."""
    return MockAppContext(make_test_collection(10))


@pytest.fixture
def tm_tmp_dir() -> "Path":
    """workspace 内临时目录（绕开 sandbox 对外部 %TEMP% 的写限制）。

    pytest 默认 tmp_path 落在 %TEMP%（workspace 外，sandbox 拒绝写入），
    改用 tests/ 下的固定临时目录，用后清理。
    """
    import shutil
    from pathlib import Path as _Path

    base = _Path(__file__).parent / "_tm_tmp"
    base.mkdir(parents=True, exist_ok=True)
    d = base / f"case_{id(object())}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)
