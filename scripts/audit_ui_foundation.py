"""Produce a stable, machine-checkable FR24 UI migration inventory.

The audit inventories existing visual debt; findings are not failures merely
because they exist.  ``--check`` fails for unreadable/invalid Python input,
duplicate records, or records whose source path disappeared.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass, replace
from fnmatch import fnmatch
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = REPO_ROOT / "src" / "transbridge" / "ui"
MARKDOWN_RENDERER = REPO_ROOT / "src" / "transbridge" / "infra" / "markdown_renderer.py"
DEFAULT_AUDIT_PATHS = (UI_ROOT, MARKDOWN_RENDERER)
HEX_PATTERN = re.compile(r"(?<![0-9A-Fa-f])#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?(?:[0-9A-Fa-f]{2})?(?![0-9A-Fa-f])")
RGB_PATTERN = re.compile(r"\brgba?\s*\(", re.IGNORECASE)
COLOR_QSS_PATTERN = re.compile(
    r"(?:^|[;{])\s*(?:color|border(?:-[a-z]+)?-color)\s*:|"
    r"background-image\s*:|(?:qlineargradient|qradialgradient|qconicalgradient)\s*\(",
    re.IGNORECASE,
)
BACKGROUND_QSS_PATTERN = re.compile(
    r"(?:^|[;{])\s*background(?:-color)?\s*:\s*([^;}\n]+)",
    re.IGNORECASE,
)
KINDS = frozenset({
    "accessibility",
    "color_qss",
    "custom_paint",
    "hex_color",
    "qsettings",
    "qt_color",
    "raw_qt_color",
    "rich_text",
    "stylesheet",
    "theme_polling",
    "theme_qsettings",
    "unbounded_theme_cache",
    "window_tree_scan",
})
FINAL_BLOCKING_KINDS = frozenset({
    "color_qss",
    "hex_color",
    "raw_qt_color",
    "theme_polling",
    "theme_qsettings",
    "unbounded_theme_cache",
    "window_tree_scan",
})
FINAL_EXCLUDED_PREFIXES = (
    "src/transbridge/ui/paratranz/",
    "src/transbridge/ui/tools/ai_translator/",
)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    path: str
    line: int
    kind: str
    subsystem: str
    risk: str
    snippet_hash: str
    status: str = "pending"
    exemption: str | None = None


@dataclass(frozen=True, slots=True)
class AuditExemption:
    rule: str
    owner: str
    reason: str
    expires_when: str
    path: str | None = None
    symbol: str | None = None

    def matches(self, rule: str, path: str, symbol: str) -> bool:
        return (
            self.rule == rule
            and (self.path is None or fnmatch(path, self.path))
            and (self.symbol is None or fnmatch(symbol, self.symbol))
        )

    @property
    def description(self) -> str:
        selector = f"path={self.path or '*'};symbol={self.symbol or '*'}"
        return f"rule={self.rule};{selector};owner={self.owner};reason={self.reason};expires_when={self.expires_when}"


FINAL_EXEMPTIONS = (
    AuditExemption(
        rule="hex_color",
        path="src/transbridge/ui/foundation/builtins.py",
        symbol="*",
        owner="UI Foundation ThemeRegistry",
        reason="declarative built-in provider source data is validated and compiled before runtime",
        expires_when="built-in providers move to validated packaged descriptors",
    ),
    AuditExemption(
        rule="unbounded_theme_cache",
        path="src/transbridge/ui/foundation/theme_service.py",
        symbol="ThemeService.*",
        owner="UI Foundation ThemeService",
        reason="palette entries are bounded by the explicitly registered theme and scheme set",
        expires_when="runtime provider installation or replacement is introduced",
    ),
    AuditExemption(
        rule="hex_color",
        path="src/transbridge/ui/workbench/labels_view.py",
        symbol="*",
        owner="Workbench label editor",
        reason="these values are user-selectable label data, not application theme presentation",
        expires_when="label colours use a typed persisted domain-data value object",
    ),
    AuditExemption(
        rule="qsettings",
        path="src/transbridge/ui/shell/window_lifecycle.py",
        symbol="*",
        owner="MainWindow geometry lifecycle",
        reason="QSettings stores window geometry only and does not own theme or locale preferences",
        expires_when="window geometry moves into the unified configuration repository",
    ),
)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _subsystem(path: Path) -> str:
    try:
        parts = path.resolve().relative_to(UI_ROOT.resolve()).parts
    except ValueError:
        return "infra/markdown_renderer" if path.resolve() == MARKDOWN_RENDERER.resolve() else "other"
    if not parts:
        return "ui"
    if parts[0] == "tools" and len(parts) > 1:
        return f"tools/{parts[1].removesuffix('.py')}"
    return parts[0].removesuffix(".py")


def _snippet_hash(lines: list[str], line: int) -> str:
    snippet = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    return hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16]


def _call_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _risk(kind: str) -> str:
    return {
        "custom_paint": "high",
        "theme_polling": "high",
        "theme_qsettings": "high",
        "unbounded_theme_cache": "high",
        "window_tree_scan": "high",
        "qsettings": "high",
        "rich_text": "high",
        "color_qss": "medium",
        "stylesheet": "medium",
        "hex_color": "medium",
        "qt_color": "medium",
        "raw_qt_color": "medium",
        "accessibility": "low",
    }[kind]


def _symbol_for(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(names)) or "<module>"


def _literal_string(node: ast.AST) -> str | None:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _contains_color_qss(qss: str) -> bool:
    if COLOR_QSS_PATTERN.search(qss):
        return True
    structural_values = {"transparent", "none", "inherit"}
    return any(
        match.group(1).strip().casefold() not in structural_values for match in BACKGROUND_QSS_PATTERN.finditer(qss)
    )


def _assignment_name(node: ast.Assign | ast.AnnAssign) -> str:
    target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _validate_exemptions(
    exemptions: tuple[AuditExemption, ...],
    *,
    require_paths: bool,
) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for exemption in exemptions:
        key = (exemption.rule, exemption.path, exemption.symbol)
        if key in seen:
            errors.append(f"duplicate final exemption: {key}")
        seen.add(key)
        if exemption.rule not in KINDS:
            errors.append(f"unknown final exemption rule: {exemption.rule}")
        if exemption.path is None and exemption.symbol is None:
            errors.append(f"final exemption requires path or symbol: {exemption.rule}")
        if not exemption.owner.strip():
            errors.append(f"final exemption requires owner: {exemption.rule}")
        if not exemption.reason.strip():
            errors.append(f"final exemption requires reason: {exemption.rule}")
        if not exemption.expires_when.strip() or exemption.expires_when.strip().casefold() in {"never", "none"}:
            errors.append(f"final exemption requires a removal condition: {exemption.rule}")
        if require_paths and exemption.path and not any(char in exemption.path for char in "*?["):
            if not (REPO_ROOT / exemption.path).exists():
                errors.append(f"final exemption path disappeared: {exemption.path}")
    return errors


def _finalize_record(
    record: AuditRecord,
    *,
    symbol: str,
    exemptions: tuple[AuditExemption, ...],
) -> tuple[AuditRecord, str | None]:
    exemption = next(
        (item for item in exemptions if item.matches(record.kind, record.path, symbol)),
        None,
    )
    if exemption is not None:
        return replace(record, status="exempt", exemption=exemption.description), None
    if record.kind in FINAL_BLOCKING_KINDS:
        blocked = replace(record, status="blocked")
        return blocked, f"final violation: {record.path}:{record.line}:{record.kind}"
    return replace(record, status="verified"), None


def _audit_source(
    path: Path,
    source: str,
    *,
    final: bool,
    exemptions: tuple[AuditExemption, ...],
) -> tuple[list[AuditRecord], list[str]]:
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [], [f"{_relative(path)}:{error.lineno or 1}: {error.msg}"]

    subsystem = _subsystem(path)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    candidates: dict[tuple[int, str], str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            symbol = _symbol_for(node, parents)
            if name == "setStyleSheet":
                candidates[(node.lineno, "stylesheet")] = symbol
                qss = _literal_string(node.args[0]) if node.args else None
                if qss is not None and _contains_color_qss(qss):
                    candidates[(node.lineno, "color_qss")] = symbol
            elif name in {"QColor", "QBrush", "QPen"}:
                candidates[(node.lineno, "qt_color")] = symbol
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, (str, int)):
                    candidates[(node.lineno, "raw_qt_color")] = symbol
            elif name in {"QSettings", "ConfigParser"}:
                candidates[(node.lineno, "qsettings")] = symbol
                context = f"{_relative(path)} {symbol} {ast.unparse(node)}".casefold()
                if "theme" in context or "locale" in context:
                    candidates[(node.lineno, "theme_qsettings")] = symbol
            elif name in {"setAccessibleName", "setAccessibleDescription"}:
                candidates[(node.lineno, "accessibility")] = symbol
            elif name in {"setHtml", "setMarkdown", "setTextFormat", "QTextDocument"}:
                candidates[(node.lineno, "rich_text")] = symbol
            elif name in {"QPainter", "drawText", "drawPixmap", "drawRect", "drawRoundedRect", "paint"}:
                candidates[(node.lineno, "custom_paint")] = symbol
            elif name == "allWidgets":
                candidates[(node.lineno, "window_tree_scan")] = symbol
            elif name == "QTimer" and any(
                marker in f"{_relative(path)} {symbol}".casefold() for marker in ("theme", "locale")
            ):
                candidates[(node.lineno, "theme_polling")] = symbol
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "paintEvent",
            "drawControl",
            "drawPrimitive",
        }:
            candidates[(node.lineno, "custom_paint")] = _symbol_for(node, parents)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            name = _assignment_name(node)
            symbol = _symbol_for(node, parents)
            empty_mapping = isinstance(value, ast.Dict) and not value.keys
            empty_mapping = empty_mapping or (
                isinstance(value, ast.Call) and _call_name(value) in {"dict", "defaultdict"} and not value.args
            )
            if (
                empty_mapping
                and "cache" in name.casefold()
                and any(marker in f"{_relative(path)} {symbol} {name}".casefold() for marker in ("theme", "palette"))
            ):
                candidates[(node.lineno, "unbounded_theme_cache")] = symbol
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if HEX_PATTERN.search(node.value) or RGB_PATTERN.search(node.value):
                candidates[(node.lineno, "hex_color")] = _symbol_for(node, parents)

    records: list[AuditRecord] = []
    errors: list[str] = []
    for (line, kind), symbol in sorted(candidates.items()):
        record = AuditRecord(
            path=_relative(path),
            line=line,
            kind=kind,
            subsystem=subsystem,
            risk=_risk(kind),
            snippet_hash=_snippet_hash(lines, line),
        )
        if final:
            record, error = _finalize_record(record, symbol=symbol, exemptions=exemptions)
            if error is not None:
                errors.append(error)
        records.append(record)
    return records, errors


def audit_source(
    relative_path: str,
    source: str,
    *,
    final: bool = False,
    exemptions: tuple[AuditExemption, ...] = (),
) -> tuple[list[AuditRecord], list[str]]:
    """Audit an in-memory source fixture without adding it to the repository."""
    path = REPO_ROOT / relative_path
    exemption_errors = _validate_exemptions(exemptions, require_paths=False) if final else []
    records, errors = _audit_source(path, source, final=final, exemptions=exemptions)
    return records, exemption_errors + errors


def audit_file(
    path: Path,
    *,
    final: bool = False,
    exemptions: tuple[AuditExemption, ...] = FINAL_EXEMPTIONS,
) -> tuple[list[AuditRecord], list[str]]:
    return _audit_source(path, path.read_text(encoding="utf-8"), final=final, exemptions=exemptions)


def _is_final_covered(path: Path, *, include_pending_migrations: bool) -> bool:
    relative = _relative(path)
    if not include_pending_migrations and any(relative.startswith(prefix) for prefix in FINAL_EXCLUDED_PREFIXES):
        return False
    try:
        path.resolve().relative_to(UI_ROOT.resolve())
    except ValueError:
        return True
    return _module_name(path) in _production_module_names()


def audit_paths(
    paths: list[Path],
    *,
    final: bool = False,
    exemptions: tuple[AuditExemption, ...] = FINAL_EXEMPTIONS,
    include_pending_migrations: bool = False,
) -> tuple[list[AuditRecord], list[str]]:
    records: list[AuditRecord] = []
    errors = _validate_exemptions(exemptions, require_paths=True) if final else []
    files: list[Path] = []
    for selected in paths:
        resolved = selected if selected.is_absolute() else REPO_ROOT / selected
        if not resolved.exists():
            errors.append(f"path does not exist: {resolved}")
            continue
        files.extend(sorted(resolved.rglob("*.py")) if resolved.is_dir() else [resolved])
    for path in sorted(set(files)):
        current, current_errors = audit_file(
            path,
            final=final and _is_final_covered(path, include_pending_migrations=include_pending_migrations),
            exemptions=exemptions,
        )
        records.extend(current)
        errors.extend(current_errors)
    records.sort(key=lambda item: (item.path, item.line, item.kind))
    keys = [(item.path, item.line, item.kind) for item in records]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    errors.extend(f"duplicate record: {path}:{line}:{kind}" for path, line, kind in duplicates)
    errors.extend(f"record path disappeared: {item.path}" for item in records if not (REPO_ROOT / item.path).is_file())
    return records, errors


def _module_name(path: Path) -> str:
    relative = path.resolve().relative_to((REPO_ROOT / "src").resolve()).with_suffix("")
    return ".".join(relative.parts)


def _resolve_import(current: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = current.rsplit(".", 1)[0]
    parts = package.split(".")
    if node.level > 1:
        parts = parts[: -(node.level - 1)]
    return ".".join([*parts, *(node.module or "").split(".")]).rstrip(".")


@lru_cache(maxsize=1)
def _production_module_names() -> frozenset[str]:
    files = {_module_name(path): path for path in UI_ROOT.rglob("*.py")}
    pending = ["transbridge.ui.app", "transbridge.ui.main_window"]
    reached: set[str] = set()
    while pending:
        module = pending.pop()
        if module in reached or module not in files:
            continue
        reached.add(module)
        tree = ast.parse(files[module].read_text(encoding="utf-8"), filename=str(files[module]))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_import(module, node)
                imports.add(base)
                imports.update(f"{base}.{alias.name}" for alias in node.names)
        pending.extend(sorted(name for name in imports if name.startswith("transbridge.ui")))
    return frozenset(reached)


def production_reachability() -> dict[str, object]:
    """Return source-level reachability from the current GUI composition roots."""

    files = {_module_name(path): path for path in UI_ROOT.rglob("*.py")}
    reached = _production_module_names()
    legacy = {}
    for name in ("transbridge.ui.workbench.step1", "transbridge.ui.workbench.step3"):
        legacy[name] = {"exists": name in files, "reachable": name in reached}
    return {
        "roots": ["transbridge.ui.app", "transbridge.ui.main_window"],
        "reachable_module_count": len(reached),
        "legacy": legacy,
    }


def build_report(
    paths: list[Path],
    *,
    final: bool = False,
    exemptions: tuple[AuditExemption, ...] = FINAL_EXEMPTIONS,
    include_pending_migrations: bool = False,
) -> tuple[dict[str, object], list[str]]:
    records, errors = audit_paths(
        paths,
        final=final,
        exemptions=exemptions,
        include_pending_migrations=include_pending_migrations,
    )
    counts: dict[str, int] = {}
    subsystem_counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
        subsystem_counts[record.subsystem] = subsystem_counts.get(record.subsystem, 0) + 1
    report: dict[str, object] = {
        "schema_version": 1,
        "mode": "final" if final else "inventory",
        "roots": ["src/transbridge/ui", "src/transbridge/infra/markdown_renderer.py"],
        "kinds": sorted(KINDS),
        "counts": dict(sorted(counts.items())),
        "subsystem_counts": dict(sorted(subsystem_counts.items())),
        "production_reachability": production_reachability(),
        "records": [asdict(record) for record in records],
        "errors": errors,
        "final": {
            "blocking_kinds": sorted(FINAL_BLOCKING_KINDS),
            "excluded_prefixes": list(FINAL_EXCLUDED_PREFIXES),
            "include_pending_migrations": include_pending_migrations,
            "blocker_count": sum(item.status == "blocked" for item in records),
            "pending_count": sum(item.status == "pending" for item in records),
            "exemptions": [asdict(item) for item in exemptions] if final else [],
        },
    }
    return report, errors


def _markdown(report: dict[str, object]) -> str:
    lines = [
        "# UI Foundation migration audit",
        "",
        f"Records: {len(report['records'])}",
        "",
        "| Path | Line | Kind | Subsystem | Risk | Status |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for item in report["records"]:
        lines.append(
            f"| `{item['path']}` | {item['line']} | {item['kind']} | {item['subsystem']} | "
            f"{item['risk']} | {item['status']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="files/directories; defaults to src/transbridge/ui")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="fail on malformed or unstable inventory records")
    parser.add_argument(
        "--final",
        action="store_true",
        help="apply final migration blockers; AI Translator and ParaTranz remain excluded until their migration lands",
    )
    parser.add_argument(
        "--include-pending-migrations",
        action="store_true",
        help="include AI Translator and ParaTranz in the final blocker set after their migration owners hand off",
    )
    args = parser.parse_args(argv)
    report, errors = build_report(
        args.paths or list(DEFAULT_AUDIT_PATHS),
        final=args.final,
        include_pending_migrations=args.include_pending_migrations,
    )
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else _markdown(report)
    )
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
        output.write_text(rendered, encoding="utf-8")
    return 1 if args.check and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
