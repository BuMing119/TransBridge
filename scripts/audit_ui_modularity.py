"""Audit FR25 UI size and dependency boundaries.

Existing god modules are explicit, expiring baseline exemptions.  New modules
and migrated files are checked normally, so the audit prevents regression
without pretending the pre-migration tree is already compliant.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = REPO_ROOT / "src" / "transbridge" / "ui"


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    path: str
    line: int
    message: str


@dataclass(frozen=True, slots=True)
class Exemption:
    path: str
    rules: frozenset[str]
    owner: str
    reason: str
    expires_when: str


EXEMPTIONS = (
    Exemption(
        "workbench/step1.py",
        frozenset({"review-module-size"}),
        "FR25-S04 Source/Parse owner",
        "compatibility facade still composes the existing source form while parsing lives in ParsePresenter",
        "FR24 shared form components replace the remaining widget construction",
    ),
    Exemption(
        "workbench/step2.py",
        frozenset({"review-module-size", "review-class-methods"}),
        "FR25-S04 table owner",
        "incremental table compatibility facade retains public filters, edit, locate and render-generation orchestration",
        "a projection-backed table model replaces the QTableWidget compatibility facade",
    ),
    Exemption(
        "workbench/cards/download_card.py",
        frozenset({"review-module-size"}),
        "FR25-S04 operation-card owner",
        "download card keeps legacy batch/download validation while execution is delegated through the card presenter",
        "download validation moves to the application command adapter",
    ),
    Exemption(
        "workbench/cards/upload_views.py",
        frozenset({"review-module-size"}),
        "FR25-S04 operation-card owner",
        "two compatibility upload dialogs share one cohesive form module and contain no execution ownership",
        "FR24 shared form components split the two dialog layouts",
    ),
    Exemption(
        "context.py",
        frozenset({"class-methods", "review-module-size", "review-class-methods"}),
        "ADR-018 compatibility owner",
        "public projection/legacy facade; new presenters use narrow feature ports",
        "all legacy AppContext consumers pass the ADR-018 retirement gate",
    ),
)


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _feature(module: str) -> str:
    parts = module.split(".")
    try:
        index = parts.index("ui")
    except ValueError:
        return ""
    tail = parts[index + 1 :]
    if not tail:
        return ""
    if tail[0] == "tools" and len(tail) > 1:
        return f"tools/{tail[1]}"
    return tail[0]


def _module_for_path(path: Path, root: Path) -> str:
    rel = path.resolve().relative_to(root.resolve()).with_suffix("")
    return "transbridge.ui." + ".".join(rel.parts)


def audit_file(path: Path, *, root: Path = UI_ROOT) -> list[Finding]:
    rel = _relative(path, root)
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [Finding("syntax", rel, error.lineno or 1, str(error))]

    findings: list[Finding] = []
    line_count = len(source.splitlines())
    if line_count > 700:
        findings.append(Finding("module-size", rel, 1, f"{line_count} physical lines exceeds hard gate 700"))
    elif line_count > 500:
        findings.append(Finding("review-module-size", rel, 1, f"{line_count} physical lines exceeds review target 500"))

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)) and isinstance(node, ast.ClassDef):
            method_count = sum(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in node.body)
            if method_count > 40:
                findings.append(
                    Finding(
                        "class-methods",
                        rel,
                        node.lineno,
                        f"class {node.name} has {method_count} methods; hard gate is 40",
                    )
                )
            elif method_count > 30:
                findings.append(
                    Finding(
                        "review-class-methods",
                        rel,
                        node.lineno,
                        f"class {node.name} has {method_count} methods; review target is 30",
                    )
                )

    current_feature = _feature(_module_for_path(path, root))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "_find_main_window",
            "_find_panel",
        }:
            findings.append(Finding("parent-lookup", rel, node.lineno, f"implicit UI lookup {node.name} is forbidden"))
        if isinstance(node, ast.While) and any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "parent"
            for child in ast.walk(node)
        ):
            findings.append(Finding("parent-lookup", rel, node.lineno, "walking QObject parents is forbidden"))
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and isinstance(node.value, ast.Attribute)
            and node.value.attr.startswith("_")
            and _root_name(node) == "self"
        ):
            findings.append(
                Finding(
                    "private-component-access",
                    rel,
                    node.lineno,
                    f"accesses collaborator private member {node.attr}",
                )
            )
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("transbridge.ui"):
            imported_feature = _feature(node.module)
            if imported_feature and imported_feature != current_feature:
                for alias in node.names:
                    if alias.name.startswith("_"):
                        findings.append(
                            Finding(
                                "private-cross-feature-import",
                                rel,
                                node.lineno,
                                f"imports private symbol {alias.name} from {node.module}",
                            )
                        )
        if isinstance(node, (ast.Import, ast.ImportFrom)) and (path.stem.endswith("_view") or path.stem == "view"):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for module in modules:
                if module.startswith(("transbridge.infra", "transbridge.persistence")):
                    findings.append(
                        Finding("view-infra-import", rel, node.lineno, f"View imports infrastructure module {module}")
                    )
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if isinstance(value, ast.Call):
            name = (
                value.func.attr
                if isinstance(value.func, ast.Attribute)
                else (value.func.id if isinstance(value.func, ast.Name) else "")
            )
            if name == "QTimer" or name.endswith(("Widget", "Window", "Dialog", "Application")):
                findings.append(
                    Finding(
                        "module-ui-singleton",
                        rel,
                        node.lineno,
                        f"module-level mutable UI object {name} requires an explicit composition owner",
                    )
                )
    return findings


def _imported_ui_modules(path: Path, *, root: Path) -> set[str]:
    module = _module_for_path(path, root)
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names if alias.name.startswith("transbridge.ui"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                if node.level > 1:
                    base = base[: -(node.level - 1)]
                target = ".".join([*base, *(node.module or "").split(".")]).rstrip(".")
            else:
                target = node.module or ""
            if target.startswith("transbridge.ui"):
                imported.add(target)
                if node.module is None:
                    imported.update(f"{target}.{alias.name}" for alias in node.names)
    return imported


def _cycle_findings(paths: list[Path], *, root: Path) -> list[Finding]:
    modules = {_module_for_path(path, root): path for path in paths}
    graph = {
        module: {target for target in _imported_ui_modules(path, root=root) if target in modules}
        for module, path in modules.items()
    }
    findings: list[Finding] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, trail: list[str]) -> None:
        if module in visiting:
            cycle = trail[trail.index(module) :] + [module]
            path = modules[module]
            finding = Finding("import-cycle", _relative(path, root), 1, " -> ".join(cycle))
            if finding not in findings:
                findings.append(finding)
            return
        if module in visited:
            return
        visiting.add(module)
        for target in graph[module]:
            visit(target, [*trail, module])
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module, [])
    return findings


def _is_exempt(finding: Finding) -> bool:
    return any(item.path == finding.path and finding.rule in item.rules for item in EXEMPTIONS)


def audit_paths(paths: list[Path], *, root: Path = UI_ROOT, include_exempt: bool = False) -> list[Finding]:
    findings = [finding for path in paths for finding in audit_file(path, root=root)]
    findings.extend(_cycle_findings(paths, root=root))
    if include_exempt:
        return findings
    return [finding for finding in findings if not _is_exempt(finding)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Python files/directories; defaults to the UI tree")
    parser.add_argument("--include-exempt", action="store_true", help="also report registered baseline exemptions")
    args = parser.parse_args(argv)
    selected = args.paths or [UI_ROOT]
    files: list[Path] = []
    for selected_path in selected:
        resolved = selected_path if selected_path.is_absolute() else REPO_ROOT / selected_path
        files.extend(resolved.rglob("*.py") if resolved.is_dir() else [resolved])
    findings = audit_paths(files, include_exempt=args.include_exempt)
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.rule}: {finding.message}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
