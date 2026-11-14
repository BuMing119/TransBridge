"""Discover full entry JSON across archive layouts without guessing file ownership."""

import json

import pytest

from tests.paratranz.test_artifact_file_downloads import setup_reader
from tests.paratranz.test_batch_download_snapshot import scope


def use_utf8(api, *, prefix="utf8/", suffix=""):
    api.extra = [(prefix + path + suffix, json.dumps(rows, ensure_ascii=False)) for path, rows in api.payloads.items()]
    api.payloads = {}


@pytest.mark.parametrize("path", ["a.esp/书籍/书籍_书名.json", "a.esp/书籍_书名.json.json"])
@pytest.mark.parametrize("prefix", ["utf8/", "", "export-2026/custom/", "export/raw/", "raw/wrapper/"])
@pytest.mark.parametrize("suffix", ["", ".json"])
def test_discovered_archive_matches_direct_snapshots(monkeypatch, path, prefix, suffix):
    extra = {path: 50, path.replace("a.esp/", "other.esp/"): 60}
    reader, backend, api, _ = setup_reader(49, extra=extra)
    # Retain empty translations, untranslated/ignored stages, and optional context.
    backend.rows[1][0]["translation"] = ""
    backend.rows[2][0].pop("context")
    use_utf8(api, prefix=prefix, suffix=suffix)

    zipped = reader.fetch(7, (scope("a"),))

    assert not zipped[0].error and len(zipped[0].entries) == 50
    assert path in zipped[0].paths and path.replace("a.esp/", "other.esp/") not in zipped[0].paths
    assert not api.destination.parent.exists()
    assert not any(call["kind"] == "download" for call in backend.calls)
    monkeypatch.setattr("transbridge.paratranz.batch_download_snapshot.ARTIFACT_FILE_THRESHOLD", 1000)
    direct, backend, _, _ = setup_reader(49, extra=extra)
    backend.rows[1][0]["translation"] = ""
    backend.rows[2][0].pop("context")
    assert direct.fetch(7, (scope("a"),)) == zipped


def test_utf8_fallback_can_coexist_with_raw_members():
    reader, _, api, _ = setup_reader(50)
    path = "a.esp/category-49.json"
    rows = api.payloads.pop(path)
    api.extra = [("utf8/" + path, json.dumps(rows))]

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert not api.destination.parent.exists()


def test_unchanged_raw_member_precedes_utf8_copy():
    reader, _, api, _ = setup_reader(50)
    path = "a.esp/category-49.json"
    rows = api.payloads.pop(path)
    api.extra = [("raw/" + path, json.dumps(rows)), ("utf8/" + path, "not entry JSON")]

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("field", ["key", "original", "translation", "stage"])
def test_utf8_missing_entry_field_is_rejected_without_defaulting(field):
    reader, _, api, _ = setup_reader(50, extra={"b.esp.json": 60})
    del api.payloads["a.esp/category-20.json"][0][field]
    use_utf8(api)

    result = reader.fetch(7, (scope("a"), scope("b")))

    assert "完整词条" in result[0].error and "category-20.json" in result[0].error
    assert not result[0].entries
    assert not result[1].error and len(result[1].entries) == 1
    assert not api.destination.parent.exists()


@pytest.mark.parametrize(
    "payload",
    [
        '{"title": "translated"}',
        '[{"key": "title", "translation": "translated"}]',
        '["translated"]',
        "null",
        "not JSON",
    ],
)
def test_formatted_utf8_outputs_are_rejected(payload):
    reader, _, api, _ = setup_reader(50)
    use_utf8(api)
    api.extra[0] = (api.extra[0][0], payload)

    result = reader.fetch(7, (scope("a"),))

    assert result[0].error and not result[0].entries
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("field,value", [("original", None), ("translation", 1), ("stage", True), ("stage", "5")])
def test_utf8_fields_keep_existing_typed_entry_validation(field, value):
    reader, _, api, _ = setup_reader(50)
    api.payloads["a.esp/category-0.json"][0][field] = value
    use_utf8(api)

    result = reader.fetch(7, (scope("a"),))

    assert result[0].error and not result[0].entries
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("member", ["utf8/other.esp/category-49.json", "utf8/category-49.json"])
def test_utf8_requires_exact_inventory_path(member):
    reader, _, api, _ = setup_reader(50)
    use_utf8(api)
    api.extra[-1] = (member, api.extra[-1][1])

    with pytest.raises(ValueError, match="缺少所选文件") as caught:
        reader.fetch(7, (scope("a"),))

    assert "category-49.json" in str(caught.value) and "已检索全包" in str(caught.value)
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("reverse", [True, False])
def test_discovery_skips_formatted_copy_and_accepts_verified_entries(reverse):
    reader, _, api, _ = setup_reader(50)
    use_utf8(api)
    name, content = api.extra[0]
    api.extra[0] = (name, '{"title": "formatted translation"}')
    api.extra.append(("outer/complete/a.esp/category-0.json.json", "\ufeff" + content))
    if reverse:
        api.extra.reverse()

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("conflict", [False, True])
def test_preferred_directory_wins_without_comparing_other_copies(conflict):
    reader, _, api, _ = setup_reader(50)
    use_utf8(api)
    rows = json.loads(api.extra[0][1])
    if conflict:
        rows[0]["translation"] = "conflicting translation"
    api.extra.append(("another/root/a.esp/category-0.json", json.dumps(rows)))

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 50
    assert all(entry.translation != "conflicting translation" for entry in result[0].entries)
    assert not api.destination.parent.exists()


@pytest.mark.parametrize("other_path", ["a.esp/title.json.json", "outer/a.esp/title.json"])
def test_discovered_members_cannot_belong_to_unselected_inventory_file(other_path):
    path = "a.esp/title.json"
    reader, backend, api, _ = setup_reader(0, extra={path: 1, other_path: 2})
    api.extra = [("export/" + other_path, json.dumps(backend.rows[1]))]
    api.payloads = {}
    artifact = reader.artifact_factory(object())
    try:
        with pytest.raises(ValueError, match="归属不明确"):
            artifact.prepare(7, backend.inventory, {1})
    finally:
        artifact.close()
    assert not api.destination.parent.exists()


def test_empty_entry_list_is_a_valid_discovered_file():
    reader, _, api, _ = setup_reader(50)
    api.payloads["a.esp/category-0.json"] = []
    use_utf8(api)

    result = reader.fetch(7, (scope("a"),))

    assert not result[0].error and len(result[0].entries) == 49 and len(result[0].paths) == 50
    assert not api.destination.parent.exists()


def test_utf8_does_not_hide_ambiguous_raw_ownership():
    path = "a.esp/title.json"
    reader, backend, api, _ = setup_reader(0, extra={path: 1, path + ".json": 2})
    del api.payloads[path + ".json"]
    api.extra = [("utf8/" + path + ".json", json.dumps(backend.rows[2]))]
    artifact = reader.artifact_factory(object())
    try:
        with pytest.raises(ValueError, match="归属不明确"):
            artifact.prepare(7, backend.inventory, {2})
    finally:
        artifact.close()
    assert not api.destination.parent.exists()
