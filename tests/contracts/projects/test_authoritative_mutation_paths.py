"""Guard the UI/task adapters that historically bypassed the V2 aggregate."""

import ast
from pathlib import Path

_ROOT = Path(__file__).parents[3] / "src" / "transbridge"


def _source(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


_MUTATION_FILES = (
    "smart_assistant/tools/tool_editor.py",
    "smart_assistant/tools/types.py",
    "ui/operations/production_support.py",
    "ui/operations/authoritative_batch_download.py",
    "ui/tools/ai_translator/task_session.py",
    "ui/tools/ai_translator/version_snapshot.py",
    "ui/tools/dictionary_panel.py",
    "ui/workbench/step2.py",
    "ui/workbench/translation_reset.py",
    "ui/workbench/translation_stage_action.py",
)

_ALLOWED_PROJECTION_WRITES = {
    ("smart_assistant/tools/tool_editor.py", "edit_translation", "translation"),
    ("smart_assistant/tools/tool_editor.py", "edit_translation", "stage"),
    ("smart_assistant/tools/tool_editor.py", "set_stage", "stage"),
    ("smart_assistant/tools/types.py", "rollback_entry_states.restore", "translation"),
    ("smart_assistant/tools/types.py", "rollback_entry_states.restore", "stage"),
    ("ui/operations/production_support.py", "replace_local_snapshots", "collection"),
    ("ui/operations/authoritative_batch_download.py", "publish", "collection"),
    ("ui/tools/ai_translator/task_session.py", "mark_completed", "collection"),
    ("ui/tools/ai_translator/version_snapshot.py", "_restore_before_entries", "translation"),
    ("ui/tools/ai_translator/version_snapshot.py", "_restore_before_entries", "stage"),
    ("ui/tools/dictionary_panel.py", "_on_apply_dict", "translation"),
    ("ui/tools/dictionary_panel.py", "_restore_collection_states", "translation"),
    ("ui/tools/dictionary_panel.py", "_restore_collection_states", "stage"),
    ("ui/workbench/step2.py", "_on_item_changed", "translation"),
    ("ui/workbench/step2.py", "_on_item_changed", "stage"),
    ("ui/workbench/translation_reset.py", "run", "translation"),
    ("ui/workbench/translation_reset.py", "run", "stage"),
    ("ui/workbench/translation_stage_action.py", "run", "stage"),
}


class _ProjectionWriteVisitor(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.functions: list[str] = []
        self.writes: set[tuple[str, str, str]] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record(target)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record(node.target)
        if node.value is not None:
            self.generic_visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record(node.target)
        self.generic_visit(node.value)

    def _record(self, target: ast.expr) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._record(item)
            return
        if isinstance(target, ast.Attribute) and target.attr in {"collection", "translation", "stage"}:
            self.writes.add((self.relative, ".".join(self.functions), target.attr))


def test_known_translation_adapters_commit_or_fail_closed_for_v2_projects() -> None:
    assert "commands.replace_entry_states(" in _source("ui/tools/dictionary_panel.py")
    assert "commands.replace_entry_states(" in _source("ui/version_persistence.py")
    assert "publish_collection_modified(" in _source("smart_assistant/tools/tool_translator.py")
    assert "commands.replace_entry_records(" in _source("ui/operations/production_support.py")
    assert "commands.replace_entry_states(" in _source("ui/workbench/translation_stage_action.py")

    task_ai = _source("ui/tools/ai_translator/task_session.py")
    assert "uses_authoritative_projection" in task_ai
    assert "self._persistence.commit_translation(entries)" in task_ai

    batch_download = _source("ui/workbench/cards/download_card.py")
    assert "uses_authoritative_projection" in batch_download
    assert "AuthoritativeBatchDownloadSession.capture" in batch_download
    assert "commands.replace_entry_records(" in _source("ui/operations/authoritative_batch_download.py")


def test_projection_divergence_guard_compares_complete_visible_and_authoritative_sets() -> None:
    context = _source("ui/context.py")
    assert "return visible != states" in context
    assert "external_refs" in context


def test_direct_projection_writes_stay_inside_reviewed_commit_or_rollback_boundaries() -> None:
    writes: set[tuple[str, str, str]] = set()
    for relative in _MUTATION_FILES:
        visitor = _ProjectionWriteVisitor(relative)
        visitor.visit(ast.parse(_source(relative), filename=relative))
        writes.update(visitor.writes)

    assert writes == _ALLOWED_PROJECTION_WRITES
