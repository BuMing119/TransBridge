"""Translation-only task payloads accepted at the SQLite boundary."""

from __future__ import annotations

from pathlib import PureWindowsPath
import re

from transbridge.application.io.identity import EntryKey, ExternalEntryRef, Provenance
from transbridge.application.projects.source_fields import business_fields, entry_fields

_TEXT_FIELDS = frozenset({
    "original",
    "translation",
    "context",
    "text",
    "before",
    "candidate",
    "review_before_text",
    "refined_translation",
    "proposed_translation",
    "base_translation",
    "expected_text",
})
_SOURCE_FILE_PATH = re.compile(
    r"(?i)(?:file:/{1,3}|[a-z]:[\\/]|\\\\|(?<![\w:/])/(?!/))[^<>\"\r\n]*?"
    r"\.(?:esp|esm|esl|xml|json|eet|sst|strings|dlstrings|ilstrings|bsa)\b"
)
_REQUEST_FIELDS = frozenset({
    "action",
    "file_type",
    "source_name",
    "source_locale",
    "target_locale",
    "source_fingerprint",
    "overwrite",
    "intensity",
    "strategy",
    "mode",
    "request_id",
    "run_id",
})
_ENTRY_FIELDS = frozenset({
    "schema_version",
    "id",
    "key",
    "entry_key",
    "revision",
    "original",
    "translation",
    "stage",
    "context",
    "requires_original_match",
    "string_id",
    "full_form_id",
    "dsd_type",
    "dsd_index",
    "dsd_editor_id",
})
_INPUT_FIELDS = frozenset({
    "entry_key",
    "original",
    "translation",
    "context",
    "context_known",
    "stage",
    "revision",
    "terminology_plugin_id",
})
_SOURCE_FIELDS = frozenset({"key", "label", "checkpoint_stem", "checkpoint_stems"})
_SCOPE_FIELDS = ("translate_keys", "polish_keys", "postprocess_keys")
_TEXT_ROWS = frozenset({"entries", "unmapped", "raw_entries", "parse_unmapped", "baseline_entries"})
_RESULT_FIELDS = frozenset({
    "entry_key",
    "source",
    "action",
    "before",
    "status",
    "text",
    "stage",
    "reason",
    "decision",
    "applied",
    "attempt",
    "report_details",
    "merge_status",
    "merge_reason",
    "decision_revision",
    "candidate_revision",
})
_RECORD_FIELDS = frozenset({
    "task_id",
    "origin_task_id",
    "owner_id",
    "session_id",
    "entrypoint",
    "project_id",
    "variant_id",
    "mode",
    "config_digest",
    "state",
    "created_at",
    "updated_at",
    "execution_config",
    "checkpoint_settings",
    "sources",
    "supported",
    "recovery_error",
    "recovery_contract",
    "overwrite",
    "execution_runs",
    "execution_inputs",
    "reference_snapshot",
    "execution_references",
    "conflict_policy",
    "request_options",
    "origin",
    "results_only",
    "result_kind",
    "label",
    "fixed_inputs",
    "source_key",
    "source_fingerprint",
    "candidate_rows",
    "entries",
    "unmapped",
    "raw_entries",
    "parse_unmapped",
    "parse_outcome",
    "parse_diagnostics",
    "registration_state",
    "registered",
    "registration_attempted",
    "merge",
    "merge_error",
    "error",
    "entry_statuses",
    "applied_entries",
    "applied",
    "project_saved",
    "saved_count",
})
_FORBIDDEN = frozenset({
    "prepared_source",
    "source_snapshot",
    "input_snapshot",
    "snapshot_content",
    "content",
    "blob",
    "$bytes",
    "bytes",
    "binary",
    "parsed_tree",
    "parser_tree",
    "template",
    "source_descriptor",
    "uri",
    "location",
})
_REPORT_FIELDS = frozenset({
    "schema",
    "run_id",
    "created_at",
    "updated_at",
    "ended_at",
    "project_id",
    "variant_id",
    "state",
    "counts",
    "applied",
    "saved",
    "project_saved",
    "snapshot_saved",
    "version_label",
    "recovery_task_id",
})
_SNAPSHOT_FIELDS = frozenset({
    "schema",
    "run_id",
    "outcome",
    "counts",
    "diagnostics",
    "issues",
    "failures",
    "timing_ms",
    "run_spec_summary",
})
_REPORT_ENTRY_FIELDS = frozenset({
    "entry_key",
    "before_revision",
    "original",
    "before",
    "candidate",
    "stage",
    "phases",
    "accepted",
    "context",
    "report_details",
    "terminology_plugin_id",
})


def task_payload(record, *, partial=False):
    if not isinstance(record, dict):
        raise ValueError("Task recovery payload must be an object")
    if record.keys() & _FORBIDDEN:
        raise ValueError("Task recovery only accepts extracted translation data")
    result = {}
    for name, value in record.items():
        if name == "sources":
            result[name] = [_source(item) for item in value]
        elif name == "execution_inputs":
            result[name] = {run: [_source(item) for item in sources] for run, sources in value.items()}
        elif name == "candidate_rows":
            result[name] = [candidate_payload(row) for row in value]
        elif name in _TEXT_ROWS:
            if name in {"entries", "unmapped"}:
                result[name] = [_entry(item) for item in value]
        elif name == "source_key":
            result[name] = _name(value)
        elif name in {"fixed_inputs", "request_options"}:
            result[name] = _selected(value, _REQUEST_FIELDS)
        elif name in _RECORD_FIELDS or name.endswith("_ignored_empty"):
            result[name] = _metadata(value, name)
        elif partial and name in {"scope_count", "execution_source_refs"}:
            continue
    return result


def candidate_payload(row):
    if not isinstance(row, dict):
        raise ValueError("Candidate must be an object")
    _require_no_material(row)
    if (
        not isinstance(row.get("text"), str)
        or type(row.get("stage")) is not int
        or not isinstance(row.get("before"), dict)
    ):
        raise ValueError("Candidate must provide translated text, stage and frozen base fields")
    result = {name: _metadata(value, name) for name, value in row.items() if name in _RESULT_FIELDS}
    result["entry_key"] = EntryKey.from_dict(row["entry_key"]).to_dict()
    if "source" in result:
        result["source"] = _name(row["source"])
    if "before" in row:
        result["before"] = _selected(row["before"], _INPUT_FIELDS | {"source_locator", "deleted"})
    return result


def report_payload(record):
    projected = _selected(record, _REPORT_FIELDS)
    projected["sources"] = [_report_source(source) for source in record.get("sources", ())]
    return projected


def _report_source(source):
    projected = _selected(source, {"key", "label", "error"})
    projected.update(key=_name(source["key"]), label=_name(source["label"]))
    if "snapshot" in source:
        snapshot = source["snapshot"]
        report = _selected(snapshot, _SNAPSHOT_FIELDS)
        report["entries"] = [_report_entry(item) for item in snapshot.get("entries", ())]
        if "stages" in snapshot:
            report["stages"] = [
                {
                    **_selected(stage, {"phase", "duration_ms", "diagnostics"}),
                    "entries": [_report_entry(item) for item in stage.get("entries", ())],
                }
                for stage in snapshot["stages"]
            ]
        projected["snapshot"] = report
    return projected


def _report_entry(item):
    if "result_reference" in item:
        raise ValueError("A report cannot supply its own candidate reference")
    entry = _selected(item, _REPORT_ENTRY_FIELDS)
    entry["entry_key"] = EntryKey.from_dict(item["entry_key"]).to_dict()
    return entry


def _entry(row):
    result = _selected(row, _ENTRY_FIELDS)
    if "metadata" in row:
        metadata = row["metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("Task entry metadata must be an object")
        result["metadata"] = entry_fields(
            (
                (name, value)
                for name, value in metadata.items()
                if name not in {"eet.native", "sst.native", "plugin.sst_locator", "transbridge.io.paratranz.extensions"}
            ),
            ignore_unknown=True,
        )
    for name, item_type in (("external_refs", ExternalEntryRef), ("provenance", Provenance)):
        if name in row:
            result[name] = [_business_item(item, item_type) for item in row[name]]
    return result


def _business_item(raw, item_type):
    item = item_type.from_dict(raw)
    output = item.to_dict()
    safe = _metadata(output["metadata"], "metadata")
    output["metadata"] = business_fields(
        (name, value) for name, value in safe.items() if not isinstance(value, (dict, list))
    )
    for name in ("source", "scope", "opaque_id"):
        if name in output:
            output[name] = _metadata(output[name], name)
    return output


def _source(row):
    source = {name: _metadata(row[name], name) for name in _SOURCE_FIELDS if name in row}
    source.update(key=_name(row["key"]), label=_name(row["label"]))
    for name in _SCOPE_FIELDS:
        if name in row:
            source[name] = [EntryKey.from_dict(key).to_dict() for key in row[name]]
    if "inputs" in row:
        source["inputs"] = [_input(item) for item in row["inputs"]]
    return source


def _input(row):
    result = _selected(row, _INPUT_FIELDS)
    if "entry" in row:
        entry = _entry(row["entry"])
        result["entry_fields"] = {name: value for name, value in entry.items() if name not in _INPUT_FIELDS}
    elif "entry_fields" in row:
        entry = _entry(row["entry_fields"])
        result["entry_fields"] = {name: value for name, value in entry.items() if name not in _INPUT_FIELDS}
    return result


def _selected(row, names):
    if not isinstance(row, dict):
        raise ValueError("Task entry must be an object")
    return {name: _metadata(value, name) for name, value in row.items() if name in names}


def _name(value):
    return PureWindowsPath(str(value)).name if "\\" in str(value) or "/" in str(value) else value


def _metadata(value, name):
    if name in _TEXT_FIELDS and type(value) is str:
        return value
    if isinstance(value, dict):
        return {
            key: _metadata(item, key)
            for key, item in value.items()
            if isinstance(key, str)
            and key.lower() not in _FORBIDDEN
            and not any(word in key.lower() for word in ("path", "bytes", "attachment", "_blob"))
        }
    if isinstance(value, (tuple, list)):
        return [_metadata(item, name) for item in value]
    if type(value) is str:
        return _SOURCE_FILE_PATH.sub(lambda match: _name(match[0]), value)
    if value is None or type(value) in {bool, int, float}:
        return value
    raise ValueError(f"Task field {name!r} must contain translation data or JSON scalars")


def _require_no_material(value):
    if isinstance(value, dict):
        for name, item in value.items():
            if not isinstance(name, str) or name.lower().replace("-", "_") in _FORBIDDEN:
                raise ValueError("Task payload contains a source container or binary material")
            _require_no_material(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _require_no_material(item)
