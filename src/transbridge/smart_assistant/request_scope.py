"""Restore captured selection without treating later content edits as a new target."""

from copy import deepcopy
import json

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.source_input_selection import repaired_selection
from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.entry_alias import ai_entry_key

_IDENTITY_FIELDS = (("active_version_identity", "_target_version_identity"),)


def execution_version(context) -> str:
    # Older receipts also contain revisions. They remain readable, but content
    # revisions belong to each actual commit's CAS, never request admission.
    version = {public: getattr(context, private, None) for public, private in _IDENTITY_FIELDS}
    override = getattr(context, "_source_input_selection", None)
    if override is not None:
        version["source_input_selection"] = deepcopy(override)
    return json.dumps(version)


def apply_captured_scope(request, ingress, context, *, require_selection: bool = True) -> None:
    source = next((i for i in ingress if i["message_id"] in request.source_message_ids), None)
    selection = source.get("selection", {}) if source else {}
    baseline = json.loads(request.execution_version_json) if request.execution_version_json else selection
    app = getattr(context, "app_context", None)
    for public, private in _IDENTITY_FIELDS:
        expected = baseline.get(public)
        actual = getattr(context, private, None)
        if isinstance(expected, list):
            expected = tuple(expected)
        projected = getattr(app, public, actual)
        if expected is not None and projected != expected and (expected or projected):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "原输入的项目或版本已变化，请核对原资源后继续")
        if expected is not None and expected != actual and (expected or actual):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "执行集合不符合已保存的原输入版本")
    collection = getattr(context, "_source_input_full_collection", None)
    if collection is None:
        collection = getattr(context, "_target_collection", None)
    if collection is not None:
        # Never fall through __getattr__ to a later active UI slot/collection.
        object.__setattr__(context, "collection", collection)
        object.__setattr__(context, "active_slot", None)
    if not require_selection:
        return
    repaired = repaired_selection(request, source, context, baseline)
    if repaired is not None:
        selection = repaired
        object.__setattr__(context, "_source_input_selection", deepcopy(baseline["source_input_selection"]))
    else:
        object.__setattr__(context, "_source_input_selection", None)
    source_scope = baseline.get("source_input_selection", {}).get("source_scope") if repaired is not None else None
    # A template operation targets complete selected sources, independently of
    # the active collection or entry identities belonging to skipped sources.
    if "selected_entry_ids" in selection and source_scope is None:
        selected = set(selection["selected_entry_ids"])
        mapped_keys = (
            {EntryKey.from_dict(value) for value in baseline["source_input_selection"].get("selected_entry_keys", ())}
            if repaired is not None
            else set()
        )
        mapped_ids = {key.serialize() if key.original is not None else key.local_key for key in mapped_keys}
        entries = (
            tuple(
                entry
                for entry in collection
                if ai_entry_key(entry) in selected
                and (ai_entry_key(entry) not in mapped_ids or entry.identity in mapped_keys)
            )
            if collection is not None
            else ()
        )
        if selected != {ai_entry_key(entry) for entry in entries} or not mapped_keys <= {
            entry.identity for entry in entries
        }:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "原输入的选中条目已不存在，请重新核对选择")
        object.__setattr__(context, "selected_entry_ids", tuple(selection["selected_entry_ids"]))
        object.__setattr__(context, "selected_entries", entries)
        if repaired is not None:
            from transbridge.converter.translation_entry_collection import TranslationEntryCollection

            object.__setattr__(context, "_source_input_full_collection", collection)
            scoped = TranslationEntryCollection(entries)
            object.__setattr__(context, "collection", scoped)
            object.__setattr__(context, "_target_collection", scoped)
    for field in ("filter_state", "translation_scope"):
        if field in selection:
            object.__setattr__(context, field, deepcopy(selection[field]))
