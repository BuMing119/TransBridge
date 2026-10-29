"""A plugin may reference quests whose title records only exist in its masters."""

from tests.paratranz.test_batch_download_snapshot import Gateway, reader
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.sync import SyncAction
from transbridge.application.sync.batch_download import BatchDownloadSource, plan_batch_download, snapshots_from_entries
from transbridge.application.sync.local_candidate import RemoteSourceIndex, build_local_candidate
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.batch_download_snapshot import BatchDownloadScope


def test_named_quest_files_download_and_match_without_local_quest_title_records():
    samples = (
        ("DA04HMYoureNotMyDad:000249C1|1~DIAL:FULL", "I am not your champion, monster."),
        ("DA04HMYoureNotMyDad:00024AA1|1~INFO:NAM1", "Who do you think brought Septimus here?"),
        ("DA04HMYoureNotMyDad:00024AA1|2~INFO:NAM1", "Your free will is an illusion."),
    )
    local = tuple(
        TranslationEntry(key, key, original, "", 0, key.partition("~")[2] + "|0002D512") for key, original in samples
    )
    rows = [
        {"id": index, "key": key, "original": original, "translation": f"测试译文{index}", "stage": 1}
        for index, (key, original) in enumerate(samples, 1)
    ]
    rows.extend(
        {"id": index, "key": f"cloud-only-{index}", "original": f"Only cloud {index}", "translation": "额外译文"}
        for index in range(4, 16)
    )
    gateway = Gateway(
        {
            "Dragonborn.esm/任务/对话_[Discerning the Transmundane].json": 1,
            "Dragonborn.esm/任务/对话_[Fight! Fight!].json": 2,
            "Skyrim.esm/任务/对话_[Discerning the Transmundane].json": 3,
        },
        {1: rows[:8], 2: rows[8:]},
    )
    scope = BatchDownloadScope("dragonborn", SourceNamespace.legacy(), ("Dragonborn.esm.json",))
    read = reader(gateway).fetch(7, (scope,))[0]
    assert not read.error and len(read.entries) == 15
    source = BatchDownloadSource(scope.source_id, "Dragonborn.esm", snapshots_from_entries(local), read.entries)
    plan = plan_batch_download((source,))
    assert plan.updated_count == 3 and plan.deferred_count == 0
    assert not any(item.action is SyncAction.CREATE_LOCAL for item in plan.plans[0].items)
    candidate = build_local_candidate(
        source.local_entries,
        RemoteSourceIndex(read.entries),
        tuple(item for item in plan.plans[0].items if item.action is SyncAction.UPDATE_LOCAL),
        attach_remote_refs=False,
        preserve_local_source=True,
    )
    assert len(candidate) == 3
    assert {entry.translation for entry in candidate} == {"测试译文1", "测试译文2", "测试译文3"}
    assert gateway.calls == [("inventory", 7), ("file", 7, 1), ("file", 7, 2)]
