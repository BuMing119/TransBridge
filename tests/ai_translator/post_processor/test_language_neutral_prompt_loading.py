"""Language-neutral post-processing prompt loading contracts."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from transbridge.ai_translator.post_processor import llm_arbiter, llm_refiner, polisher, quality_gate
from transbridge.config.language_profiles import LanguageProfile

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "data" / "prompts"


class _UnusedLLM:
    def chat(self, messages, max_tokens=0):  # pragma: no cover - constructors do not call the model
        raise AssertionError("LLM should not be called while loading prompts")


@pytest.mark.parametrize("stage", ["quality_gate", "refinement", "polish", "arbitration"])
def test_repository_maintains_one_default_template_per_postprocess_stage(stage: str) -> None:
    stage_dir = _PROMPTS_DIR / stage

    assert sorted(path.name for path in stage_dir.glob("*.toml")) == ["default.toml"]


@pytest.mark.parametrize(
    "stage",
    ["translation", "extraction", "quality_gate", "refinement", "polish", "arbitration"],
)
def test_default_templates_do_not_duplicate_native_output_schema(stage: str) -> None:
    with (_PROMPTS_DIR / stage / "default.toml").open("rb") as stream:
        rendered = repr(tomllib.load(stream))

    for fragment in (
        '"results":',
        '"entry_id":',
        '"verdict":',
        '"refined_translation":',
        '"polished_translation":',
        "strict JSON object",
        "Output exactly one JSON object",
    ):
        assert fragment not in rendered


@pytest.mark.parametrize(
    ("module", "factory", "stage", "rendered_system"),
    [
        (
            quality_gate,
            lambda: quality_gate.QualityGateChecker(_UnusedLLM(), target_lang="ja_JP"),
            "quality_gate",
            lambda instance: instance._render_system(instance._prompts["single_system"], "test.quality_gate"),
        ),
        (
            llm_refiner,
            lambda: llm_refiner.LLMRefiner(_UnusedLLM(), target_lang="ja_JP"),
            "refinement",
            lambda instance: instance._render_system(instance._prompts["system"], "test.refinement"),
        ),
        (
            polisher,
            lambda: polisher.LLMPolisher(_UnusedLLM(), target_lang="ja_JP"),
            "polish",
            lambda instance: instance._prompts["system_rendered"],
        ),
        (
            llm_arbiter,
            lambda: llm_arbiter.LLMArbiter(_UnusedLLM(), target_lang="ja_JP"),
            "arbitration",
            lambda instance: instance._prompts["system_rendered"],
        ),
    ],
)
def test_stage_loaders_use_default_template_and_inject_requested_language(
    monkeypatch: pytest.MonkeyPatch,
    module,
    factory,
    stage: str,
    rendered_system,
) -> None:
    loaded_paths: list[Path] = []

    def fake_load_toml(path: Path) -> dict:
        loaded_paths.append(path)
        if path.parent.name == "games":
            return {"game": {"name": "Example Game"}}
        return {}

    monkeypatch.setattr(module, "_get_prompts_dir", lambda: _PROMPTS_DIR)
    monkeypatch.setattr(module, "_load_toml", fake_load_toml)
    monkeypatch.setattr(
        module,
        "load_language_profile",
        lambda *_args, **_kwargs: LanguageProfile("ja_JP", "日本語", "French", "Japanese"),
    )

    instance = factory()
    system = rendered_system(instance)

    assert _PROMPTS_DIR / stage / "default.toml" in loaded_paths
    assert _PROMPTS_DIR / stage / "ja_JP.toml" not in loaded_paths
    assert instance._prompts["ctx"] == {
        "game_name": "Example Game",
        "source_lang": "French",
        "target_lang": "Japanese",
    }
    assert "French" in system
    assert "Japanese" in system
    assert '"results":' not in system
    assert '"entry_id":' not in system
