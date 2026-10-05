"""Convert local validation evidence into bounded, entry-scoped recovery guidance."""

from collections.abc import Iterable
import re

from transbridge.application.contracts import Diagnostic
from transbridge.application.io import EntryKey

_INSTRUCTIONS = {
    "PROOFREAD_RESPONSE_MISSING_KEY": (
        "The previous response omitted this entry. Return its entry_key and final_translation."
    ),
    "PROOFREAD_RESPONSE_DUPLICATE_KEY": "The previous response repeated this entry_key. Return it exactly once.",
    "PROOFREAD_RESPONSE_EMPTY_TRANSLATION": "The previous final_translation was empty. Return a nonempty translation.",
    "PROOFREAD_PROTECTED_SYNTAX_MISMATCH": (
        "The previous translation changed protected syntax. Preserve the placeholders and program tags from original."
    ),
    "PROOFREAD_RESPONSE_MALFORMED": (
        "The previous response could not be read reliably. Return one JSON object with a results array, "
        "without duplicate JSON object members or surrounding commentary."
    ),
}
_SCHEMA_INSTRUCTION = (
    "The previous response did not meet the output schema. Return entry_key and a nonempty final_translation; "
    "do not copy input-only fields into the result."
)
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def recovery_feedback(keys: Iterable[EntryKey], diagnostics: Iterable[Diagnostic]) -> list[dict]:
    """Use only known validation facts with explicit ownership, never raw exception text."""
    issues_by_key: dict[EntryKey, list[dict]] = {key: [] for key in keys}
    for diagnostic in diagnostics:
        if diagnostic.code not in _INSTRUCTIONS and diagnostic.code != "PROOFREAD_RESPONSE_SCHEMA_INVALID":
            continue
        details = dict(diagnostic.details)
        identities = (details["entry_key"],) if "entry_key" in details else details.get("entry_keys", ())
        affected = set()
        for identity in identities:
            try:
                key = EntryKey.from_dict(identity)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if key in issues_by_key:
                affected.add(key)
        if not affected:
            continue
        if diagnostic.code == "PROOFREAD_RESPONSE_SCHEMA_INVALID":
            # Item paths are trustworthy only for explicitly entry-scoped diagnostics.
            issues = _schema_issues(details.get("validation_details", {})) if "entry_key" in details else []
            issues = issues or [{"instruction": _SCHEMA_INSTRUCTION}]
        else:
            issues = [{"instruction": _INSTRUCTIONS[diagnostic.code]}]
        for key in affected:
            for issue in issues:
                if issue not in issues_by_key[key] and len(issues_by_key[key]) < 8:
                    issues_by_key[key].append(issue)
    return [{"entry_key": key.to_dict(), "issues": issues} for key, issues in issues_by_key.items() if issues]


def _schema_issues(details: dict) -> list[dict]:
    issues = []
    for error in details.get("errors", (details,)):
        rule = error.get("validator")
        if rule not in {"required", "additionalProperties", "type"}:
            continue
        issue = {
            "rule": rule,
            "instruction": {
                "required": "Supply the missing output fields, including final_translation when missing.",
                "additionalProperties": "Remove the unexpected fields; return only fields in the output schema.",
                "type": "Use an entry_key object with string namespace/local_key and a string final_translation.",
            }[rule],
        }
        path = error.get("path")
        if path in {"<root>", "entry_key", "entry_key/namespace", "entry_key/local_key", "final_translation"}:
            issue["path"] = path
        for field in ("missing_fields", "unexpected_fields"):
            names = error.get(field, ())
            # Field names come from model output; keep compact identifiers as data, not instructions.
            safe = [name for name in names if isinstance(name, str) and _FIELD.fullmatch(name)][:12]
            if safe:
                issue[field] = safe
        if issue not in issues:
            issues.append(issue)
        if len(issues) == 8:
            break
    return issues
