"""Offline contract for project policy source rows and the five independent upload operations."""

import pytest

from transbridge.application.file_upload import UPLOAD_MODES
from transbridge.application.file_upload_modes import UploadOperation
from transbridge.application.upload_policy import ProjectRole, TranslationMode, UploadLayout
from transbridge.application.upload_policy_codec import parse_upload_policy, upload_policy_entries


def rows(values=None):
    fields = {"policy.version": "1", "upload.layout": "categorized", "upload.translation_mode": "fill_empty,force"}
    fields.update(values or {})
    return [{"key": key, "original": value, "translation": ""} for key, value in fields.items()]


def granted(policy, user_id=101, role=ProjectRole.ADMIN):
    return {op.value for op in UploadOperation if policy.permits_operation(op, user_id=user_id, role=role)}


def test_distinct_administrators_exclusions_and_project_mode_switch():
    fields = {
        "upload.orig_only": "user:101",
        "upload.trans_fill_empty": "role:admin, role:owner",
        "upload.trans_fill_empty.deny": "user:103",
        "upload.trans_force": "user:102",
    }
    policy = parse_upload_policy(rows(fields))
    assert granted(policy, 101) == {"orig_only", "trans_fill_empty"}
    assert granted(policy, 102) == {"trans_fill_empty", "trans_force"}
    assert granted(policy, 103) == set()
    assert granted(policy, 104, ProjectRole.PROOFREADER) == set()
    fields["upload.translation_mode"] = "fill_empty"
    assert granted(parse_upload_policy(rows(fields)), 102) == {"trans_fill_empty"}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("fill_empty", {"orig_only", "trans_fill_empty"}),
        ("safe_import", {"orig_only", "both", "trans_safe"}),
        ("force", {"orig_only", "trans_force"}),
    ],
)
def test_one_write_method_never_implies_another(mode, expected):
    policy = parse_upload_policy(
        rows({"upload.translation_mode": mode, **{f"upload.{op}": "user:101" for op in UPLOAD_MODES}})
    )
    assert granted(policy) == expected


@pytest.mark.parametrize("operation", UPLOAD_MODES)
def test_granting_one_operation_only_grants_that_operation(operation):
    policy = parse_upload_policy(
        rows({"upload.translation_mode": "force,safe_import,fill_empty", f"upload.{operation}": "project_member"})
    )
    assert granted(policy) == {operation}
    assert granted(policy, role=None) == set()


@pytest.mark.parametrize("layout", UploadLayout)
def test_source_round_trip_ignores_row_order_translations_and_platform_fields(layout):
    entries = rows({
        "upload.layout": layout.value,
        "upload.trans_safe": "none",
        "upload.orig_only": "role:owner, user:101",
        "upload.orig_only.deny": "role:translator",
    })
    policy = parse_upload_policy([dict(row, translation="role:admin", stage=5) for row in reversed(entries)])
    assert policy.layout is layout
    assert policy.translation_modes == {TranslationMode.FILL_EMPTY, TranslationMode.FORCE}
    assert granted(policy, 101, ProjectRole.TRANSLATOR) == set()
    assert granted(policy, 101, ProjectRole.PROOFREADER) == {"orig_only"}
    assert granted(policy, 102, ProjectRole.OWNER) == {"orig_only"}
    assert granted(policy, 102, ProjectRole.ADMIN) == set()
    canonical = upload_policy_entries(policy)
    assert canonical == upload_policy_entries(parse_upload_policy(entries))
    assert all(row["translation"] == "" for row in canonical)
    assert parse_upload_policy(canonical) == policy


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("policy.version", "2"),
        ("policy.version", 1),
        ("upload.layout", "ordinary"),
        ("upload.translation_mode", "allow_force"),
        ("upload.translation_mode", "force,"),
        ("upload.translation_mode", ""),
        ("upload.trans_fill_empty", "none,user:1"),
        ("upload.orig_only", "user:0"),
        ("upload.orig_only", "user:-1"),
        ("upload.orig_only", "user:abc"),
        ("upload.orig_only", "user:１"),
        ("upload.trans_force.deny", "role:admins"),
        ("upload.trans_force.deny", "project_member,,role:admin"),
        ("upload.source.allow", "project_member"),
    ],
)
def test_invalid_rule_names_and_values_identify_the_rule(key, value):
    with pytest.raises(ValueError) as error:
        parse_upload_policy(rows({key: value}))
    assert key in str(error.value)


@pytest.mark.parametrize("key", ["policy.version", "upload.layout", "upload.translation_mode"])
def test_required_project_rules_and_duplicate_keys(key):
    entries = rows()
    with pytest.raises(ValueError, match=key):
        parse_upload_policy([row for row in entries if row["key"] != key])
    with pytest.raises(ValueError, match=key):
        parse_upload_policy([*entries, next(row for row in entries if row["key"] == key)])


@pytest.mark.parametrize("payload", [None, {}, [], [None], [{"key": []}], [{"key": "upload.layout"}]])
def test_invalid_document_or_row(payload):
    with pytest.raises(ValueError):
        parse_upload_policy(payload)
