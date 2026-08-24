from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

SCRIPT = Path(__file__).parents[3] / "scripts" / "audit_ui_modularity.py"
SPEC = spec_from_file_location("audit_ui_modularity", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
audit = module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def test_audit_flags_new_large_class_parent_lookup_and_private_cross_feature_import(tmp_path) -> None:
    root = tmp_path / "ui"
    target = root / "tools" / "example" / "view.py"
    target.parent.mkdir(parents=True)
    methods = "\n".join(f"    def method_{index}(self): pass" for index in range(41))
    padding = "\n".join("# padding" for _ in range(710))
    target.write_text(
        "from transbridge.ui.workbench.step2 import _PRIVATE\n"
        "from transbridge.infra.client import Client\n"
        "class Example:\n"
        f"{methods}\n"
        "    def _find_main_window(self): pass\n"
        f"{padding}\n",
        encoding="utf-8",
    )

    rules = {finding.rule for finding in audit.audit_file(target, root=root)}

    assert rules == {
        "class-methods",
        "module-size",
        "parent-lookup",
        "private-cross-feature-import",
        "view-infra-import",
    }


def test_audit_flags_parent_walk_private_collaborator_and_module_ui_singleton(tmp_path) -> None:
    root = tmp_path / "ui"
    target = root / "feature" / "panel.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "from PyQt6.QtCore import QTimer\n"
        "TIMER = QTimer()\n"
        "class Panel:\n"
        "    def run(self):\n"
        "        self._host._ctx.refresh()\n"
        "        parent = self.parent()\n"
        "        while parent is not None:\n"
        "            parent = parent.parent()\n",
        encoding="utf-8",
    )

    rules = {finding.rule for finding in audit.audit_file(target, root=root)}

    assert {"module-ui-singleton", "parent-lookup", "private-component-access"} <= rules


def test_audit_paths_flags_relative_ui_import_cycle(tmp_path) -> None:
    root = tmp_path / "ui"
    package = root / "feature"
    package.mkdir(parents=True)
    first = package / "first.py"
    second = package / "second.py"
    first.write_text("from .second import Second\nclass First: pass\n", encoding="utf-8")
    second.write_text("from .first import First\nclass Second: pass\n", encoding="utf-8")

    findings = audit.audit_paths([first, second], root=root, include_exempt=True)

    assert any(finding.rule == "import-cycle" for finding in findings)


def test_audit_accepts_small_feature_local_view(tmp_path) -> None:
    root = tmp_path / "ui"
    target = root / "feature" / "example_view.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass State:\n    value: str\n",
        encoding="utf-8",
    )

    assert audit.audit_file(target, root=root) == []


def test_every_exemption_has_owner_reason_and_expiry() -> None:
    assert audit.EXEMPTIONS
    for exemption in audit.EXEMPTIONS:
        assert exemption.owner
        assert exemption.reason
        assert exemption.expires_when
