"""Freeze UI-dependent selection and advance versions only after this request's commits."""

from copy import deepcopy
import json

from transbridge.application.assistant_requests.models import RequestError

_VERSION_FIELDS = (
    ("active_version_identity", "_target_version_identity"),
    ("project_revision", "_target_project_revision"),
    ("variant_revision", "_target_variant_revision"),
)


def execution_version(context) -> str:
    return json.dumps({public: getattr(context, private, None) for public, private in _VERSION_FIELDS})


def apply_captured_scope(request, ingress, context) -> None:
    source = next((i for i in ingress if i["message_id"] in request.source_message_ids), None)
    selection = source.get("selection", {}) if source else {}
    baseline = json.loads(request.execution_version_json) if request.execution_version_json else selection
    app = getattr(context, "app_context", None)
    for public, private in _VERSION_FIELDS:
        expected = baseline.get(public)
        actual = getattr(context, private, None)
        if isinstance(expected, list):
            expected = tuple(expected)
        projected = getattr(app, public, actual)
        if expected is not None and projected != expected and (expected or projected):
            raise RequestError("REQUEST_SCOPE_MISMATCH", "原输入的项目或版本已变化，请核对原资源后继续")
        if expected is not None and expected != actual and (expected or actual):
            if not request.execution_version_json or projected != expected:
                raise RequestError("REQUEST_SCOPE_MISMATCH", "执行集合不符合已保存的原输入版本")
            object.__setattr__(context, private, expected)
    collection = getattr(context, "_target_collection", None)
    if collection is not None:
        # Never fall through __getattr__ to a later active UI slot/collection.
        object.__setattr__(context, "collection", collection)
        object.__setattr__(context, "active_slot", None)
        if request.execution_version_json:
            object.__setattr__(
                context,
                "_committed_entry_states",
                {entry.identity: (entry.translation, entry.stage) for entry in collection},
            )
    if "selected_entry_ids" in selection:
        selected = set(selection["selected_entry_ids"])
        entries = tuple(entry for entry in collection if str(entry.key) in selected) if collection is not None else ()
        if selected != {str(entry.key) for entry in entries}:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "原输入的选中条目已不存在，请重新核对选择")
        object.__setattr__(context, "selected_entry_ids", tuple(selection["selected_entry_ids"]))
        object.__setattr__(context, "selected_entries", entries)
    for field in ("filter_state", "translation_scope"):
        if field in selection:
            object.__setattr__(context, field, deepcopy(selection[field]))
