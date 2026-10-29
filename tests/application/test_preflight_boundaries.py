"""Application preflight policies remain usable without a GUI dependency."""

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from transbridge.application.preflight.ai import AiPreflightCode, preflight_ai_run
from transbridge.application.preflight.operation import (
    DomainPreflightResult,
    OperationKind,
    OperationPreflightFacts,
    OperationPreflightService,
    PreflightCheckState,
    PreflightCheckStatus,
)


def test_preflight_imports_without_ui_or_qt() -> None:
    script = """
import importlib.abc
import sys

class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("transbridge.ui", "PyQt6")):
            raise AssertionError(f"GUI dependency: {fullname}")

sys.meta_path.insert(0, NoGui())
from transbridge.application.preflight import ai, operation
assert ai.AiPreflightResult().ready
assert operation.OperationPreflightService
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("changes", "blocked"),
    [
        ({"input_ready": False}, "INPUT"),
        ({"output_ready": False}, "OUTPUT"),
        ({"overwrite_risk": True}, "OVERWRITE"),
        ({"backup_required": True}, "BACKUP"),
    ],
)
def test_existing_shared_checks_keep_their_blocking_codes(changes, blocked) -> None:
    facts = OperationPreflightFacts("a" * 64, "revision", **changes)
    checked = OperationPreflightService().evaluate(OperationKind.WRITE, facts)
    assert not checked.ready
    assert [item.check_id for item in checked.checks if item.status is PreflightCheckStatus.BLOCKED] == [blocked]


def test_overwrite_and_backup_evidence_and_locked_hidden_warning_keep_existing_semantics() -> None:
    facts = OperationPreflightFacts(
        "a" * 64,
        "revision",
        overwrite_risk=True,
        overwrite_confirmed=True,
        backup_required=True,
        backup_enabled=True,
        locked_count=2,
        hidden_count=3,
    )
    checked = OperationPreflightService().evaluate(OperationKind.WRITE, facts)
    assert checked.ready
    stage = checked.checks[-1]
    assert stage.check_id == "LOCKED_HIDDEN"
    assert stage.status is PreflightCheckStatus.WARNING
    assert stage.reason == "锁定 2，隐藏 3"
    fomod = OperationPreflightService().evaluate(OperationKind.FOMOD, facts)
    assert [item.check_id for item in fomod.checks] == ["INPUT", "OUTPUT", "OVERWRITE", "BACKUP"]


def test_domain_evidence_and_estimated_impact_are_retained() -> None:
    facts = OperationPreflightFacts("a" * 64, "revision", estimated_impact=(("objects", 3),))
    check = PreflightCheckState("DOMAIN", "domain", PreflightCheckStatus.BLOCKED, "source changed")
    service = OperationPreflightService()
    checked = service.evaluate(OperationKind.WRITE, facts, DomainPreflightResult((check,), (("objects", 7),)))
    assert not checked.ready
    assert checked.checks[-1] is check
    assert checked.estimated_impact == (("objects", 7),)
    assert service.evaluate(OperationKind.WRITE, facts, (check,)).estimated_impact == facts.estimated_impact


@pytest.mark.parametrize("mode", ["translate", "mixed", "batch"])
def test_ai_translation_accepts_project_content_without_external_source(mode) -> None:
    config = SimpleNamespace(api_key="key", model="model", retrieval_enabled=False)
    checked = preflight_ai_run(mode, config, (object(),), dependency_available=lambda _: True)
    assert checked.ready


def test_ai_credentials_scope_and_dependency_checks_remain_blocking() -> None:
    checked = preflight_ai_run("translate", SimpleNamespace(), (), dependency_available=lambda _: False)
    assert {issue.code for issue in checked.blocking_issues} == {
        AiPreflightCode.MISSING_API_KEY,
        AiPreflightCode.MISSING_MODEL,
        AiPreflightCode.EMPTY_SCOPE,
        AiPreflightCode.MISSING_DEPENDENCY,
    }
    assert checked.blocking_reason


def test_ai_local_embedding_uses_injected_model_evidence_and_preserves_optional_failures(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "modules.json").write_text("{}", encoding="utf-8")
    config = SimpleNamespace(
        api_key="key", model="model", embedding=SimpleNamespace(mode="local", local_model_id="local")
    )
    checked = preflight_ai_run(
        "translate",
        config,
        (object(),),
        dependency_available=lambda _: True,
        local_model_path=lambda _: model,
    )
    assert checked.ready
    missing = preflight_ai_run(
        "translate", config, (object(),), dependency_available=lambda _: True, local_model_path=lambda _: None
    )
    assert not missing.ready
    assert not missing.blocking_issues
    assert [issue.code for issue in missing.optional_issues] == [AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION]


def test_existing_result_validation_rejects_duplicate_domain_check_ids() -> None:
    facts = OperationPreflightFacts("a" * 64, "revision")
    duplicate = PreflightCheckState("INPUT", "domain input", PreflightCheckStatus.PASSED)
    with pytest.raises(ValueError, match="unique"):
        OperationPreflightService().evaluate(OperationKind.FOMOD, facts, (duplicate,))
