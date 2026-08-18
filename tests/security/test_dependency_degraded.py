"""Optional dependency degradation and disabled-retrieval zero-load
(release-hardening-v2 Story 04).

A missing optional distribution (rank-bm25 / FAISS / py7zr / rarfile) must be
surfaced as a degraded capability rather than a silent fallback or a hard
import crash, and disabled retrieval must never construct or load a corpus or
vector index.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.application.capabilities import CapabilityState
from transbridge.dependency_capabilities import DEPENDENCY_BASELINE, DependencyCapability, probe_dependency


def _story_state(capability: DependencyCapability) -> CapabilityState:
    """Story-level interpretation: a missing *optional* dependency degrades the
    feature (it still exists, at reduced capability) rather than removing it."""
    report = probe_dependency(capability)
    if report.state is CapabilityState.AVAILABLE:
        return CapabilityState.AVAILABLE
    return CapabilityState.DEGRADED


# rank-bm25 / FAISS are the semantic-retrieval optional deps; py7zr / rarfile
# are the archive backends.
_OPTIONAL_DEPS: tuple[tuple[str, str, str], ...] = (
    ("hybrid-term-retrieval", "rank-bm25", "rank_bm25"),
    ("vector-retrieval", "faiss-cpu", "faiss"),
    ("7z-archive", "py7zr", "py7zr"),
    ("rar-archive", "rarfile", "rarfile"),
)


@pytest.mark.parametrize(("feature", "dist", "import_name"), _OPTIONAL_DEPS)
def test_present_dependency_is_available(feature: str, dist: str, import_name: str) -> None:
    state = _story_state(DependencyCapability(feature, dist, import_name))
    assert state is CapabilityState.AVAILABLE


@pytest.mark.parametrize(("feature", "dist", "import_name"), _OPTIONAL_DEPS)
def test_missing_dependency_is_degraded(
    feature: str,
    dist: str,
    import_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from transbridge import dependency_capabilities as dc

    def no_spec(_name: str):
        return None

    monkeypatch.setattr(dc, "find_spec", no_spec)
    capability = DependencyCapability(feature, dist, import_name)

    assert _story_state(capability) is CapabilityState.DEGRADED
    report = probe_dependency(capability)
    assert dist in report.missing_prerequisites


def test_find_spec_error_is_degraded_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    from transbridge import dependency_capabilities as dc

    def boom(_name: str):
        raise ImportError("metadata unavailable")

    monkeypatch.setattr(dc, "find_spec", boom)
    capability = DependencyCapability("hybrid-term-retrieval", "rank-bm25", "rank_bm25")

    report = probe_dependency(capability)
    assert report.state is CapabilityState.UNAVAILABLE
    assert "rank-bm25" in report.missing_prerequisites


def test_baseline_covers_archive_and_retrieval_backends() -> None:
    import_names = {cap.import_name for cap in DEPENDENCY_BASELINE}
    assert {"rank_bm25", "py7zr", "rarfile"} <= import_names


def test_never_installed_module_naturally_degrades() -> None:
    # A distribution that is genuinely not installed anywhere on this host
    # drives the real find_spec path (no monkeypatch) to a non-available state.
    capability = DependencyCapability("never-present-feature", "not-a-real-dist-xyz", "not_a_real_module_xyz_123")
    state = _story_state(capability)
    assert state is CapabilityState.DEGRADED


def test_disabled_retrieval_loads_no_corpus_or_vector(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from transbridge.ai_translator import term_database
    from transbridge.config.llm import LLMConfig

    constructions = 0

    class CorpusSpy:
        def __init__(self, _path: str) -> None:
            nonlocal constructions
            constructions += 1

        def load(self) -> None:
            raise AssertionError("disabled retrieval must not load a corpus")

    monkeypatch.setattr(term_database, "DynamicTermDatabase", CorpusSpy)
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))

    config = SimpleNamespace(
        retrieval_enabled=False,
        enable_semantic_match=True,
        embedding=SimpleNamespace(mode="api"),
        term_priority=["dynamic"],
    )

    manager = term_database.TermDatabaseManager(config, "fixture.esp")
    monkeypatch.setattr(
        manager,
        "_init_vector_index",
        lambda: (_ for _ in ()).throw(AssertionError("disabled retrieval initialized vector")),
    )

    assert manager.load_all() == {}
    assert constructions == 0
