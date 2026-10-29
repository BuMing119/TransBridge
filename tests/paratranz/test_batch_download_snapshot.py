"""Batch reads are file-bounded, source-isolated and recover ordinary ID churn."""

from threading import Event

import pytest

from transbridge.application.io.identity import SourceNamespace
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.paratranz.batch_download_snapshot import BatchDownloadScope, BatchDownloadSnapshotReader
from transbridge.paratranz.file_transport import UploadCancellation


class Gateway:
    def __init__(self, inventory, responses):
        self.inventories = inventory if isinstance(inventory, list) else [inventory]
        self.responses = responses
        self.calls = []
        self.closed = 0

    def inventory(self, project_id):
        self.calls.append(("inventory", project_id))
        inventory = self.inventories.pop(0) if len(self.inventories) > 1 else self.inventories[0]
        if isinstance(inventory, Exception):
            raise inventory
        return inventory

    def get_file_translation(self, project_id, file_id):
        self.calls.append(("file", project_id, file_id))
        response = self.responses[file_id]
        if isinstance(response, Exception):
            raise response
        return response() if callable(response) else response

    def close(self):
        self.closed += 1


def scope(source_id, *paths):
    return BatchDownloadScope(source_id, SourceNamespace(f"source:{source_id}"), paths or (f"{source_id}.esp.json",))


def row(*, remote_id=None, file_id=None, translation="译文"):
    result = {"key": "shared", "original": "Original", "translation": translation, "stage": 3}
    if remote_id is not None:
        result["id"] = remote_id
    if file_id is not None:
        result["fileId"] = file_id
    return result


def reader(gateway):
    return BatchDownloadSnapshotReader(object(), gateway_factory=lambda _config: gateway)


@pytest.mark.parametrize("size", [1, 1_000, 10_000])
def test_inventory_and_complete_file_reads_do_not_grow_with_entries(size):
    rows = [dict(row(), key=f"entry-{index}") for index in range(size)]
    gateway = Gateway(
        {"a.esp.json": 1, "b.esp/人名.json": 2, "a.esp_1.json": 3, "unselected.esp.json": 4},
        {1: rows, 2: rows, 3: rows},
    )
    result = reader(gateway).fetch(7, (scope("a"), scope("b", "b.esp.json", "b.esp/人名.json")))

    assert [len(item.entries) for item in result] == [size * 2, size]
    assert {entry.entry_key.namespace for entry in result[0].entries} == {SourceNamespace("source:a")}
    assert {entry.entry_key.namespace for entry in result[1].entries} == {SourceNamespace("source:b")}
    assert gateway.calls == [("inventory", 7), ("file", 7, 1), ("file", 7, 3), ("file", 7, 2)]
    assert gateway.closed == 1


def test_plugin_directory_includes_renamed_quests_and_other_categories_but_not_other_roots():
    gateway = Gateway(
        {
            "a.esp/任务/当前.json": 1,
            "a.esp/任务/旧名称.json": 2,
            "a.esp/任务/当前_2.json": 3,
            "a.esp/其他.json": 4,
            "other/a.esp.json": 5,
        },
        {file_id: [dict(row(), key=f"entry-{file_id}")] for file_id in (1, 2, 3, 4)},
    )

    result = reader(gateway).fetch(7, (scope("a", "a.esp.json", "a.esp/任务/当前.json"),))

    assert result[0].paths == (
        "a.esp/任务/当前.json",
        "a.esp/任务/旧名称.json",
        "a.esp/任务/当前_2.json",
        "a.esp/其他.json",
    )
    assert {entry.entry_key.local_key for entry in result[0].entries} == {f"entry-{index}" for index in (1, 2, 3, 4)}
    assert len(gateway.calls) == 5


def test_source_with_failed_second_file_has_no_partial_entries_but_other_source_completes():
    gateway = Gateway(
        {"a.esp.json": 1, "a.esp_1.json": 2, "b.esp.json": 3},
        {1: [row()], 2: ValueError("无效完整文件"), 3: [row(translation="B译文")]},
    )

    first, second = reader(gateway).fetch(7, (scope("a"), scope("b")))

    assert first.entries == () and first.error
    assert second.entries[0].translation == "B译文" and not second.error
    assert gateway.closed == 1


@pytest.mark.parametrize("failure", ["file", "authorization", "cancel"])
def test_failed_source_retains_file_prefix_as_explicitly_incomplete_evidence(failure):
    cancel, retained = Event(), []

    def first_file():
        if failure == "cancel":
            cancel.set()
        return [row(remote_id=1)]

    error = (
        ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "revoked", status=403)
        if failure == "authorization"
        else ValueError("second file unavailable")
    )
    gateway = Gateway({"a.esp.json": 1, "a.esp_1.json": 2}, {1: first_file, 2: error})

    def fetch():
        return reader(gateway).fetch(
            7,
            (scope("a"),),
            cancellation=UploadCancellation(cancel),
            on_source=lambda source, read: retained.append((source, read)),
        )

    if failure == "file":
        assert fetch()[0].entries == ()
    else:
        with pytest.raises(Exception):
            fetch()
    assert len(retained) == 1
    assert retained[0][1].paths == ("a.esp.json",)
    assert retained[0][1].entries[0].translation == "译文"
    assert "未完整读取" in retained[0][1].error


def test_missing_file_is_an_empty_source_not_a_read_error():
    gateway = Gateway({"b.esp.json": 2}, {2: [row()]})
    first, second = reader(gateway).fetch(7, (scope("a"), scope("b")))
    assert first.entries == first.paths == () and first.error == ""
    assert len(second.entries) == 1


def test_empty_new_layout_file_still_wins_over_translated_legacy_file():
    gateway = Gateway({"a.esp.json": 1, "a.json": 2}, {1: [], 2: [row(translation="旧版译文")]})

    result = reader(gateway).fetch(7, (scope("a"),))[0]

    assert result.paths == ("a.esp.json",) and not result.entries and not result.error
    assert gateway.calls == [("inventory", 7), ("file", 7, 1)]


def test_legacy_relocation_keeps_its_generation_when_a_new_layout_appears_midread():
    missing = ExternalServiceError(ExternalServiceCategory.NOT_FOUND, "removed", status=404)
    gateway = Gateway(
        [{"a.json": 1}, {"a_1.json": 2, "a.esp.json": 3}],
        {1: missing, 2: [row(translation="完整旧布局")], 3: [row(translation="新布局")]},
    )

    first = reader(gateway).fetch(7, (scope("a"),))[0]

    assert first.paths == ("a_1.json",) and first.entries[0].translation == "完整旧布局"
    assert not first.error and ("file", 7, 3) not in gateway.calls
    next_run = reader(gateway).fetch(7, (scope("a"),))[0]
    assert next_run.paths == ("a.esp.json",) and next_run.entries[0].translation == "新布局"


def test_deleted_and_recreated_path_is_relocated_once_and_uses_current_id():
    missing = ExternalServiceError(ExternalServiceCategory.NOT_FOUND, "removed", status=404)
    gateway = Gateway(
        [{"a.esp.json": 1, "b.esp.json": 3}, {"a.esp.json": 2, "b.esp.json": 3}],
        {1: missing, 2: [row(translation="新文件译文")], 3: [row()]},
    )

    result = reader(gateway).fetch(7, (scope("a"), scope("b")))

    assert result[0].entries[0].translation == "新文件译文"
    assert not any(item.error for item in result)
    assert gateway.calls == [("inventory", 7), ("file", 7, 1), ("inventory", 7), ("file", 7, 2), ("file", 7, 3)]


def test_repeated_path_churn_is_bounded_and_does_not_discard_other_sources():
    missing = ExternalServiceError(ExternalServiceCategory.NOT_FOUND, "removed", status=404)
    gateway = Gateway(
        [{"a.esp.json": 1, "b.esp.json": 3}, {"a.esp.json": 2, "b.esp.json": 3}],
        {1: missing, 2: missing, 3: [row()]},
    )
    first, second = reader(gateway).fetch(7, (scope("a"), scope("b")))
    assert first.error and not first.entries
    assert len(second.entries) == 1
    assert sum(call[0] == "inventory" for call in gateway.calls) == 2


def test_relocation_rebuilds_the_complete_source_when_new_partitions_appear():
    missing = ExternalServiceError(ExternalServiceCategory.NOT_FOUND, "removed", status=404)
    gateway = Gateway(
        [
            {"a.esp.json": 1, "a.esp/人名.json": 2, "b.esp.json": 5},
            {"a.esp.json": 1, "a.esp/人名_1.json": 3, "a.esp/人名_2.json": 4, "b.esp.json": 5},
        ],
        {
            1: [dict(row(remote_id=10), key="root")],
            2: missing,
            3: [dict(row(remote_id=30), key="part-1")],
            4: [dict(row(remote_id=40), key="part-2")],
            5: [row(remote_id=50)],
        },
    )
    first, second = reader(gateway).fetch(7, (scope("a", "a.esp.json", "a.esp/人名.json"), scope("b")))

    assert not first.error
    assert first.paths == ("a.esp.json", "a.esp/人名_1.json", "a.esp/人名_2.json")
    assert [entry.entry_key.local_key for entry in first.entries] == ["root", "part-1", "part-2"]
    assert len(second.entries) == 1
    assert gateway.calls.count(("file", 7, 1)) == 1
    assert gateway.calls == [
        ("inventory", 7),
        ("file", 7, 1),
        ("file", 7, 2),
        ("inventory", 7),
        ("file", 7, 3),
        ("file", 7, 4),
        ("file", 7, 5),
    ]


def test_relocation_discards_successful_prefix_when_a_new_partition_is_invalid():
    missing = ExternalServiceError(ExternalServiceCategory.NOT_FOUND, "removed", status=404)
    gateway = Gateway(
        [
            {"a.esp.json": 1, "a.esp/人名.json": 2, "b.esp.json": 5},
            {"a.esp.json": 1, "a.esp/人名_1.json": 3, "a.esp/人名_2.json": 4, "b.esp.json": 5},
        ],
        {1: [row()], 2: missing, 3: [row()], 4: {"truncated": []}, 5: [row()]},
    )
    first, second = reader(gateway).fetch(7, (scope("a", "a.esp.json", "a.esp/人名.json"), scope("b")))
    assert first.entries == () and first.paths == ()
    assert "人名_2.json" in first.error
    assert len(second.entries) == 1
    assert gateway.calls.count(("file", 7, 1)) == 1


def test_removed_path_does_not_fall_back_to_an_unrelated_same_key_file():
    missing = ExternalServiceError(ExternalServiceCategory.NOT_FOUND, "removed", status=404)
    gateway = Gateway([{"a.esp.json": 1}, {"other/a.esp.json": 2}], {1: missing})
    result = reader(gateway).fetch(7, (scope("a"),))
    assert "a.esp.json" in result[0].error and not result[0].entries
    assert gateway.calls == [("inventory", 7), ("file", 7, 1), ("inventory", 7)]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_global_auth_and_rate_errors_remain_actionable_global_errors(status):
    failure = ExternalServiceError(ExternalServiceCategory.AUTHORIZATION, "账号不可用", status=status)
    gateway = Gateway({"a.esp.json": 1, "b.esp.json": 2}, {1: failure, 2: [row()]})
    with pytest.raises(ExternalServiceError) as caught:
        reader(gateway).fetch(7, (scope("a"), scope("b")))
    assert caught.value is failure
    assert gateway.calls == [("inventory", 7), ("file", 7, 1)]
    assert gateway.closed == 1


def test_cancellation_during_file_read_never_returns_a_successful_prefix():
    event = Event()

    def cancel_during_read():
        event.set()
        return [row()]

    gateway = Gateway({"a.esp.json": 1, "b.esp.json": 2}, {1: cancel_during_read, 2: [row()]})
    with pytest.raises(ExternalServiceError) as caught:
        reader(gateway).fetch(7, (scope("a"), scope("b")), cancellation=UploadCancellation(event))
    assert caught.value.category is ExternalServiceCategory.CANCELLED
    assert gateway.calls == [("inventory", 7), ("file", 7, 1)]
    assert gateway.closed == 1


@pytest.mark.parametrize(
    "rows, message",
    [
        ({"items": []}, "词条数据无效"),
        ([row(file_id=99)], "以外"),
        ([row(remote_id=1), row(remote_id=1)], "重复词条 ID"),
    ],
)
def test_invalid_complete_response_is_scoped_and_names_the_failed_path(rows, message):
    gateway = Gateway({"a.esp.json": 1, "b.esp.json": 2}, {1: rows, 2: [row()]})
    first, second = reader(gateway).fetch(7, (scope("a"), scope("b")))
    assert first.entries == () and message in first.error and "a.esp.json" in first.error
    assert len(second.entries) == 1


def test_duplicate_remote_identity_across_sources_invalidates_both_sources():
    gateway = Gateway(
        {"a.esp.json": 1, "b.esp.json": 2, "c.esp.json": 3},
        {1: [row(remote_id=8)], 2: [row(remote_id=8)], 3: [row(remote_id=9)]},
    )
    first, second, third = reader(gateway).fetch(7, (scope("a"), scope("b"), scope("c")))
    assert first.entries == second.entries == ()
    assert "重复词条 ID" in first.error and "重复词条 ID" in second.error
    assert len(third.entries) == 1


def test_overlapping_source_file_masks_never_read_the_ambiguous_file():
    gateway = Gateway({"a_1.json": 1, "c.esp.json": 3}, {3: [row()]})
    first, second, third = reader(gateway).fetch(7, (scope("a", "a.json"), scope("b", "a_1.json"), scope("c")))
    assert "多个插件" in first.error and "多个插件" in second.error
    assert first.entries == second.entries == ()
    assert len(third.entries) == 1
    assert gateway.calls == [("inventory", 7), ("file", 7, 3)]


def test_complete_snapshot_limit_discards_the_source_without_truncating_rows():
    gateway = Gateway({"a.esp.json": 1, "b.esp.json": 2}, {1: [row(), row()], 2: [row()]})
    first, second = reader(gateway).fetch(7, (scope("a"), scope("b")), limit=1)
    assert not first.entries and "上限" in first.error
    assert len(second.entries) == 1


def test_invalid_scope_and_duplicate_namespace_are_rejected_before_remote_read():
    gateway = Gateway({}, {})
    with pytest.raises(ValueError, match="路径无效"):
        scope("a", "../a.json")
    with pytest.raises(ValueError, match="来源身份不唯一"):
        reader(gateway).fetch(7, (scope("a"), BatchDownloadScope("b", SourceNamespace("source:a"), ("b.json",))))
    assert not gateway.calls
