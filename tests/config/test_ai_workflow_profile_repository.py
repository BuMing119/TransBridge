from __future__ import annotations

import json
from pathlib import Path

import pytest

from transbridge.application.translation.custom_workflow_profile import (
    CustomWorkflowProfile,
    CustomWorkflowProfileDocument,
    WorkflowProfileValidationError,
)
from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository


def _profile(name: str) -> CustomWorkflowProfile:
    return CustomWorkflowProfile.create(
        name,
        base_mode="translate",
        workflow={"enable_post_process": True},
        limits={
            "max_concurrent": 3,
            "max_tokens_per_batch": 2000,
            "max_output_tokens": 0,
            "max_terms_per_batch": 50,
        },
    )


def test_missing_repository_is_empty_and_crud_round_trips(tmp_path: Path) -> None:
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    assert repository.load() == CustomWorkflowProfileDocument.empty()

    first = _profile("first")
    second = _profile("second")
    repository.upsert(first)
    repository.upsert(second, select=True)

    assert repository.selected() == second
    assert repository.profiles() == (first, second)
    repository.rename(second.id, "renamed")
    assert repository.selected().name == "renamed"  # type: ignore[union-attr]
    repository.delete(second.id)
    assert repository.selected() == first
    repository.delete(first.id)
    assert repository.load() == CustomWorkflowProfileDocument.empty()


def test_export_selected_uses_portable_envelope(tmp_path: Path) -> None:
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    first = _profile("first")
    second = _profile("second")
    repository.save(CustomWorkflowProfileDocument(second.id, (first, second)))

    exported_path = repository.export_file(tmp_path / "export.json", profile_id=second.id)
    exported = AiWorkflowProfileRepository.parse_file(exported_path)

    assert exported.selected_profile_id == second.id
    assert exported.profiles == (second,)


def test_parse_file_has_no_side_effect_and_valid_import_replaces_atomically(tmp_path: Path) -> None:
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    original = _profile("original")
    replacement = _profile("replacement")
    repository.save(CustomWorkflowProfileDocument(original.id, (original,)))
    source = tmp_path / "import.json"
    source.write_text(
        json.dumps(CustomWorkflowProfileDocument(replacement.id, (replacement,)).to_dict()),
        encoding="utf-8",
    )

    parsed = repository.parse_file(source)
    assert parsed.profiles == (replacement,)
    assert repository.load().profiles == (original,)

    repository.import_file(source)
    assert repository.load() == parsed


def test_invalid_import_does_not_change_existing_repository(tmp_path: Path) -> None:
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    original = _profile("original")
    repository.save(CustomWorkflowProfileDocument(original.id, (original,)))
    before = repository.path.read_bytes()
    source = tmp_path / "invalid.json"
    payload = CustomWorkflowProfileDocument(original.id, (original,)).to_dict()
    payload["profiles"][0]["api_key"] = "must-not-import"  # type: ignore[index]
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkflowProfileValidationError, match="forbidden"):
        repository.import_file(source)

    assert repository.path.read_bytes() == before
    assert repository.load().profiles == (original,)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.json"
    source.write_text(
        '{"document_type":"transbridge.ai_workflow_profiles","schema_version":1,'
        '"schema_version":1,"selected_profile_id":null,"profiles":[]}',
        encoding="utf-8",
    )

    with pytest.raises(WorkflowProfileValidationError, match="duplicate JSON object key"):
        AiWorkflowProfileRepository.parse_file(source)
