# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Restore captured selection without treating later content edits as a new target."""

from copy import deepcopy
from dataclasses import replace
import json

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.source_input_selection import repaired_selection
from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.entry_alias import ai_entry_key

_IDENTITY_FIELDS = (("active_version_identity", "_target_version_identity"),)
WORKBENCH_SCOPE_TOOLS = frozenset({
    "get_statistics",
    "list_labels",
    "get_app_state",
    "set_filters",
    "get_current_filters",
    "get_visible_entries",
    "select_entries",
    "manage_entry_labels",
    "edit_translation",
    "set_stage",
    "save_dictionary",
})


def execution_version(context, previous: str = "") -> str:
    # Older receipts also contain revisions. They remain readable, but content
    # revisions belong to each actual commit's CAS, never request admission.
    version = {public: getattr(context, private, None) for public, private in _IDENTITY_FIELDS}
    if previous:
        saved = json.loads(previous)
        for field in ("workbench_filter", "translation_scope"):
            if field in saved:
                version[field] = saved[field]
    override = getattr(context, "_source_input_selection", None)
    if override is not None:
        version["source_input_selection"] = deepcopy(override)
    return json.dumps(version)


def with_scope_change(request, context, field, state):
    from .tools.workbench_rows import workbench_source_identity

    version = json.loads(request.execution_version_json or execution_version(context))
    version[field] = {
        "revision": request.revision,
        "work_round_id": request.work_round_id,
        "source_key": context._target_source_key,
        "source_identity": workbench_source_identity(context.app_context),
        "state": state,
    }
    return replace(request, execution_version_json=json.dumps(version, ensure_ascii=False))


def _selected_candidates(collection, selected, *, workbench):
    if collection is None or not selected:
        return ()
    if workbench and callable(read := getattr(collection, "freeze_entries", None)):
        namespace = collection.source.info.namespace
        keys = [EntryKey(namespace, key) for key in selected]
        for key in selected:
            if key.startswith("["):
                try:
                    exact = EntryKey.deserialize(key)
                except ValueError:
                    continue  # A literal source key can also start with a bracket.
                if exact.namespace == namespace and exact.original is not None:
                    keys.append(exact)
        return read()(keys)
    return collection


def apply_captured_scope(request, ingress, context, *, require_selection: bool = True, workbench: bool = False) -> None:
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
    override = baseline.get("translation_scope") if request.execution_version_json else None
    if override and (override["revision"], override["work_round_id"]) == (request.revision, request.work_round_id):
        from .tools.workbench_rows import workbench_source_identity

        if override["source_key"] != getattr(app, "active_key", None) or override[
            "source_identity"
        ] != workbench_source_identity(app):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "翻译范围的来源已变化，请重新核对。")
        selection = {**selection, "translation_scope": override["state"]}
    if workbench:
        from .tools.workbench_rows import workbench_source_identity

        override = baseline.get("workbench_filter")
        if override and (override["revision"], override["work_round_id"]) == (request.revision, request.work_round_id):
            selection = {**selection, "filter_state": override["state"], "workbench_source_key": override["source_key"]}
            if "source_identity" in override:
                selection["workbench_source_identity"] = override["source_identity"]
        if "workbench_source_key" in selection:
            expected = selection["workbench_source_key"]
            if expected != getattr(context, "_target_source_key", None) or expected != getattr(app, "active_key", None):
                raise RequestError("REQUEST_SCOPE_MISMATCH", "工作台来源已切换，请重新确认筛选或选区。")
        if "workbench_source_identity" in selection and selection[
            "workbench_source_identity"
        ] != workbench_source_identity(app):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "工作台来源已被替换，请重新确认筛选或选区。")
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
        entries = tuple(
            entry
            for entry in _selected_candidates(collection, selection["selected_entry_ids"], workbench=workbench)
            if ai_entry_key(entry) in selected
            and (ai_entry_key(entry) not in mapped_ids or entry.identity in mapped_keys)
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
