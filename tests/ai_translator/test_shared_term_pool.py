from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter
from transbridge.ai_translator.project_terminology_adapter import (
    EnabledEffectiveTerminologyGate,
    ProjectTerminologyAdapter,
)
from transbridge.ai_translator.shared_term_lookup import shared_terms_for_entry
from transbridge.ai_translator.shared_term_pool import SharedTermPool, SharedTermSnapshot
from transbridge.ai_translator.term_database import DynamicTermDatabase, TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry, load_terms_json
from transbridge.ai_translator.term_publication_conflict import TermPublicationConflict
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.config.llm import LLMConfig
from transbridge.persistence.v2.models import ReadOnlyWriteRefused


@pytest.fixture
def project_terms(tmp_path, monkeypatch):
    def ai_directory(stem):
        directory = tmp_path / "legacy" / stem
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(ai_directory))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    pool = SharedTermPool("project", "variant", config.target_lang, path=tmp_path / "shared.json")

    def manager(plugin="one.esp", adapter=None, *, selected_pool=pool):
        manager = TermDatabaseManager(
            config,
            plugin,
            shared_term_pool=selected_pool,
            generated_term_plugin_id=plugin,
            effective_loader=adapter,
            terminology_context=TerminologyLookupContext("project", "variant"),
        )
        manager.load_all()
        return manager

    return SimpleNamespace(pool=pool, manager=manager, config=config, path=tmp_path / "shared.json")


def _entry(original, plugin):
    return SimpleNamespace(key="entry", original=original, form_id_with_plugin=f"000001|{plugin}")


def _decision(term, translation, *, plugin=None, suppressed=False):
    scope = TermScope.project() if plugin is None else TermScope.plugin(plugin)
    return TermDecision(
        term_id(project_id="project", variant_id="variant", scope=scope, original=term),
        "project",
        "variant",
        term,
        normalize_original(term),
        translation,
        scope=scope,
        status=DecisionStatus.ADOPTED,
        suppressed=suppressed,
    )


def _adapter(*decisions):
    snapshot = EffectiveTerminologySnapshot(
        "project", "variant", EffectiveSnapshotStatus.READY, "v1", "digest", decisions
    )
    return ProjectTerminologyAdapter(
        SnapshotEffectiveTerminologyPort(SimpleNamespace(snapshot=lambda *_args: snapshot)),
        EnabledEffectiveTerminologyGate(),
    )


def test_managers_for_different_plugins_see_only_committed_project_terms(project_terms):
    one, two = project_terms.manager(), project_terms.manager("two.esp")
    before = project_terms.pool.snapshot()
    GeneratedTermWriter(one).save([("Riverwood", "溪木镇", "auto_dialogue", "")])

    assert one.get_dynamic_db() is two.get_dynamic_db()
    assert before.entries == ()
    assert before.revision == 0
    assert two.match_terms_for_entry(_entry("Travel to Riverwood", "two.esp")) == {"Riverwood": "溪木镇"}
    assert two.load_all() == {"Riverwood": "溪木镇"}
    assert project_terms.pool.revision == 1
    assert asdict(project_terms.pool.changes_since(0)[0]) == {
        "revision": 1,
        "term": "Riverwood",
        "translation": "溪木镇",
        "plugin_id": None,
    }


def test_published_global_and_plugin_decisions_override_generated_and_shadowing_is_preserved(project_terms):
    writer = GeneratedTermWriter(project_terms.manager())
    writer.save([("Sword", "生成剑", "auto_name", ""), ("Shield", "生成盾", "auto_name", "")])
    adapter = _adapter(
        _decision("Sword", "项目剑"),
        _decision("Sword", "插件剑", plugin="one.esp"),
        _decision("Shield", "停用盾", plugin="one.esp", suppressed=True),
    )
    one, two = project_terms.manager(adapter=adapter), project_terms.manager("two.esp", adapter)
    entry_one, entry_two = _entry("Sword and Shield", "one.esp"), _entry("Sword and Shield", "two.esp")
    assert one.match_terms_for_entry(entry_one) == {"Sword": "插件剑"}
    assert two.match_terms_for_entry(entry_two) == {"Sword": "项目剑", "Shield": "生成盾"}
    assert shared_terms_for_entry(one, entry_one) == {"Sword": "插件剑"}
    assert shared_terms_for_entry(two, entry_two) == {"Sword": "项目剑", "Shield": "生成盾"}


def test_published_plugin_special_is_not_copied_to_shared_global_pool(project_terms):
    adapter = _adapter(_decision("Sword", "插件剑", plugin="one.esp"))
    one = project_terms.manager(adapter=adapter)
    GeneratedTermWriter(one).save([("Sword", "插件剑", "existing_name", "")])
    assert project_terms.pool.snapshot().entries == ()
    GeneratedTermWriter(one).save([("Sword", "错误剑", "auto_name", "")])
    assert project_terms.pool.snapshot().entries == ()
    assert project_terms.pool.conflicts_since(0)[0].plugin_id == "one.esp"
    assert project_terms.pool.conflicts_since(0)[0].accepted_translation == "插件剑"


def test_explicit_generated_plugin_scope_does_not_leak_to_other_plugins(project_terms):
    one = project_terms.manager()
    writer = GeneratedTermWriter(one)
    writer.save([("Sword", "通用剑", "auto_name", "")])
    writer.save([("Sword", "专属剑", "auto_name", "")], scope_plugin_id="one.esp")
    assert one.match_terms_for_entry(_entry("Sword", "one.esp")) == {"Sword": "专属剑"}
    assert one.match_terms_for_entry(_entry("Sword", "two.esp")) == {"Sword": "通用剑"}
    assert one.resolve_term("Sword", one.lookup_context_for_entry(_entry("Sword", "one.esp"))).translation == "专属剑"
    assert len(project_terms.pool.as_list()) == 2
    assert len(project_terms.pool.snapshot().entries) == 1
    unqualified_entry = SimpleNamespace(key="unqualified", original="Sword")
    assert one.match_terms_for_entry(unqualified_entry) == {"Sword": "专属剑"}
    assert one._snapshot_identity(one._terminology_context.for_plugin("one.esp")) != one._snapshot_identity(
        one._terminology_context.for_plugin("two.esp")
    )


def test_concurrent_conflicting_generations_keep_first_value_and_persist_diagnostic(project_terms):
    writers = [GeneratedTermWriter(project_terms.manager(plugin)) for plugin in ("one.esp", "two.esp")]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(writer.save, [("Riverwood", translation, "auto_name", "")])
            for writer, translation in zip(writers, ("溪木镇", "河木镇"), strict=True)
        ]
        for future in futures:
            future.result(timeout=5)
    snapshot = project_terms.pool.snapshot()
    assert len(snapshot.entries) == 1
    conflict = project_terms.pool.conflicts_since(0)[0]
    assert conflict.accepted_translation == snapshot.entries[0].translation
    assert conflict.proposed_translation != conflict.accepted_translation
    assert json.loads(project_terms.path.read_text(encoding="utf-8"))["conflicts"] == [asdict(conflict)]
    assert load_terms_json(project_terms.path, source=None) == project_terms.pool.as_list()


def test_failed_persistence_never_publishes_to_other_manager(project_terms, monkeypatch):
    one, two = project_terms.manager(), project_terms.manager("two.esp")
    observed = []

    def fail(_payload):
        raise OSError("storage unavailable")

    monkeypatch.setattr(project_terms.pool, "_write_bytes", fail)
    with pytest.raises(OSError, match="storage unavailable"):
        GeneratedTermWriter(one).save([("Sword", "剑", "auto_name", "")], observer=lambda *_: observed.append(True))
    assert two.resolve_term("Sword") is None
    assert project_terms.pool.revision == 0
    assert observed == []


def test_failed_acknowledgement_rolls_back_disk_and_publication(project_terms):
    one = project_terms.manager()

    def reject(*_args):
        raise RuntimeError("ownership check failed")

    with pytest.raises(RuntimeError, match="ownership check failed"):
        GeneratedTermWriter(one).save([("Sword", "剑", "auto_name", "")], observer=reject)
    assert one.resolve_term("Sword") is None
    assert not project_terms.path.exists()
    assert project_terms.pool.revision == 0
    GeneratedTermWriter(one).save([("Sword", "剑", "auto_name", "")])
    assert one.resolve_term("Sword").translation == "剑"


def test_readers_wait_until_persisted_terms_are_acknowledged(project_terms):
    one, two = project_terms.manager(), project_terms.manager("two.esp")
    persisted, release, attempted = (threading.Event() for _ in range(3))

    def acknowledge(*_args):
        persisted.set()
        assert release.wait(5)

    def read():
        attempted.set()
        return two.resolve_term("Sword")

    with ThreadPoolExecutor(max_workers=2) as executor:
        write = executor.submit(GeneratedTermWriter(one).save, [("Sword", "剑", "auto_name", "")], observer=acknowledge)
        assert persisted.wait(3)
        read_future = executor.submit(read)
        try:
            assert attempted.wait(3)
            assert not read_future.done()
        finally:
            release.set()
        write.result(timeout=5)
        assert read_future.result(timeout=5).translation == "剑"


def test_recovery_preserves_revision_scope_and_conflicts(project_terms):
    writer = GeneratedTermWriter(project_terms.manager())
    writer.save([("Sword", "剑", "auto_name", "")])
    writer.save([("Sword", "错误", "auto_name", "")])
    writer.save([("Sword", "刀", "auto_name", "")], scope_plugin_id="one.esp")
    restored = SharedTermPool("project", "variant", "zh_CN", path=project_terms.path)
    assert restored.snapshot() == project_terms.pool.snapshot()
    assert restored.snapshot("one.esp") == project_terms.pool.snapshot("one.esp")
    assert restored.conflicts_since(0) == project_terms.pool.conflicts_since(0)
    assert restored.changes_since(0) == project_terms.pool.changes_since(0)
    assert project_terms.manager(selected_pool=restored).match_terms_for_entry(_entry("Sword", "one.esp")) == {
        "Sword": "刀"
    }


@pytest.mark.parametrize(
    "identity", [("other", "variant", "zh_CN"), ("project", "other", "zh_CN"), ("project", "variant", "en")]
)
def test_project_variant_and_language_cannot_restore_another_pool(project_terms, identity):
    GeneratedTermWriter(project_terms.manager()).save([("Sword", "剑", "auto_name", "")])
    with pytest.raises(ValueError, match="another Project/Variant/language"):
        SharedTermPool(*identity, path=project_terms.path)


def test_legacy_plugin_terms_remain_local_and_are_not_migrated(project_terms):
    local = DynamicTermDatabase("one.esp")
    local.add_many_and_save([("Sword", "旧插件剑", "manual", "")])
    one, two = project_terms.manager(), project_terms.manager("two.esp")
    assert one.resolve_term("Sword").translation == "旧插件剑"
    assert two.resolve_term("Sword") is None
    assert project_terms.pool.snapshot().entries == ()
    GeneratedTermWriter(two).save([("Sword", "其他插件生成剑", "auto_name", "")])
    assert one.match_terms_for_entry(_entry("Sword", "one.esp")) == {"Sword": "旧插件剑"}
    assert shared_terms_for_entry(one, _entry("Sword", "one.esp")) == {"Sword": "旧插件剑"}
    assert shared_terms_for_entry(two, _entry("Sword", "two.esp")) == {"Sword": "其他插件生成剑"}


def test_external_shared_write_skips_publication_without_losing_task_references(project_terms, caplog):
    writer = GeneratedTermWriter(project_terms.manager())
    writer.save([("Sword", "剑", "auto_name", "")])
    snapshot = project_terms.pool.snapshot()
    external = SharedTermPool("project", "variant", "zh_CN", path=project_terms.path)
    external.publish([("Shield", "盾", "auto_name", "")])
    external_bytes = project_terms.path.read_bytes()
    acknowledged = []
    assert writer.save([("Bow", "弓", "auto_name", "")], observer=lambda *_: acknowledged.append(True)) == 0
    assert project_terms.path.read_bytes() == external_bytes
    assert project_terms.pool.changes_since(1) == ()
    assert project_terms.pool.snapshot() is snapshot
    assert acknowledged == []
    conflicts = project_terms.pool.conflicts_since(1)
    assert len(conflicts) == 1
    assert conflicts[0].term == "Bow"
    assert conflicts[0].reason == "external-write-conflict"
    assert "任务继续" in caplog.text
    with pytest.raises(TermPublicationConflict, match="其他任务修改"):
        project_terms.pool.load()


def test_external_edit_during_candidate_validation_is_not_overwritten(project_terms):
    external = SharedTermPool("project", "variant", "zh_CN", path=project_terms.path)

    def authority(_term):
        external.publish([("Shield", "盾", "auto_name", "")])
        return None, False

    with pytest.raises(TermPublicationConflict, match="其他任务修改"):
        project_terms.pool.publish([("Sword", "剑", "auto_name", "")], authority=authority)
    assert project_terms.pool.snapshot().entries == ()
    assert {entry.term for entry in load_terms_json(project_terms.path)} == {"Shield"}


def test_snapshot_mutation_cannot_change_published_values(project_terms):
    GeneratedTermWriter(project_terms.manager()).save([("Sword", "剑", "auto_name", "")])
    snapshot = project_terms.pool.snapshot()
    snapshot.entries[0].translation = "外部篡改"
    snapshot.entries[0].metadata["shared_plugin_id"] = "other.esp"
    assert project_terms.pool.snapshot().entries[0].translation == "剑"
    assert project_terms.pool.snapshot().entries[0].metadata["shared_plugin_id"] is None


def test_snapshot_records_freeze_nested_metadata_and_detach_compatibility_copies():
    term = TermEntry("Sword", "剑", "test", variants=["Blade"], metadata={"extra": {"tags": ["one"]}})
    snapshot = SharedTermSnapshot(1, (term,))
    term.translation = "changed"
    term.variants.append("Changed")
    term.metadata["extra"]["tags"].append("changed")
    record = snapshot.records[0]
    assert record.translation == "剑"
    assert record.variants == ("Blade",)
    assert record.metadata["extra"]["tags"] == ("one",)
    with pytest.raises(FrozenInstanceError):
        record.translation = "changed"
    with pytest.raises(TypeError):
        record.metadata["extra"]["tags"] = ()
    with pytest.raises(TypeError):
        snapshot.records_by_term["Sword"] = record
    detached = snapshot.entries[0]
    detached.metadata["extra"]["tags"].append("local edit")
    assert snapshot.entries[0].metadata == {"extra": {"tags": ["one"]}}


def test_snapshot_identity_reuses_scopes_and_survives_bounded_eviction(project_terms):
    pool = project_terms.pool
    pool.publish([("Sword", "剑", "auto", "")])
    pool.publish([("Sword", "特例剑", "auto", "")], plugin_id="one.esp")
    first = pool.snapshot("one.esp")
    assert pool.snapshot("one.esp") is first
    for index in range(20):
        assert pool.snapshot(f"other-{index}.esp").records[0].translation == "剑"
    assert len(pool._snapshots._views) == 16
    recreated = pool.snapshot("one.esp")
    assert recreated is not first
    assert recreated.cache_identity == first.cache_identity
    assert recreated.records[0] is first.records[0]
    assert recreated.records[0].translation == "特例剑"
    assert pool.snapshot().cache_identity != first.cache_identity


def test_resolve_generated_term_retains_detached_term_entry_contract(project_terms):
    manager = project_terms.manager()
    project_terms.pool.publish([("Sword", "剑", "auto", "")])
    result = manager.resolve_term("Sword")
    assert isinstance(result, TermEntry)
    result.translation = "detached edit"
    result.metadata["shared_plugin_id"] = "changed.esp"
    assert manager.resolve_term("Sword").translation == "剑"
    assert project_terms.pool.snapshot().records[0].metadata["shared_plugin_id"] is None


def test_rejected_publication_restores_snapshot_identity_without_aliasing_next_transaction(project_terms):
    pool = project_terms.pool
    before = pool.snapshot("one.esp")
    rejected = []

    def reject(_):
        rejected.append(pool.snapshot("one.esp"))
        raise RuntimeError("reject")

    with pytest.raises(RuntimeError, match="reject"):
        pool.publish([("Sword", "剑", "auto", "")], acknowledge=reject)
    assert pool.snapshot("one.esp") is before
    pool.publish([("Shield", "盾", "auto", "")])
    after = pool.snapshot("one.esp")
    assert after.revision == rejected[0].revision == 1
    assert after.cache_identity != rejected[0].cache_identity
    assert tuple(after.records_by_term) == ("Shield",)
    assert tuple(rejected[0].records_by_term) == ("Sword",)


def test_request_overlay_uses_the_supplied_snapshot_without_future_terms(project_terms):
    manager = project_terms.manager()
    before = project_terms.pool.snapshot("one.esp")
    GeneratedTermWriter(manager).save([("Sword", "剑", "auto_name", "")])
    entry = _entry("Sword", "one.esp")
    assert shared_terms_for_entry(manager, entry, snapshot=before) == {}
    assert shared_terms_for_entry(manager, entry) == {"Sword": "剑"}


def test_default_path_is_isolated_by_project_variant_and_target_language(tmp_path, monkeypatch):
    monkeypatch.setattr("transbridge.config.paths.get_data_dir", lambda: str(tmp_path))
    identities = [("one", "v", "zh_CN"), ("two", "v", "zh_CN"), ("one", "v2", "zh_CN"), ("one", "v", "en")]
    pools = [SharedTermPool(*identity) for identity in identities]
    assert len({Path(pool._path) for pool in pools}) == 4
    pools[0].publish([("Sword", "剑", "auto_name", "")])
    assert all(pool.snapshot().entries == () for pool in pools[1:])


def test_writer_returns_only_accepted_shared_records(project_terms):
    writer = GeneratedTermWriter(project_terms.manager())
    assert writer.save([]) == 0
    assert writer.save([("Sword", "剑", "auto_name", ""), ("Sword", "剑", "auto_name", "")]) == 1
    assert writer.save([("Sword", "剑", "auto_name", "")]) == 0
    assert writer.save([("Sword", "刀", "auto_name", "")]) == 0
    assert writer.save([("Shield", "盾", "auto_name", ""), ("", "无效", "auto_name", "")]) == 1


def test_legacy_writer_count_excludes_invalid_and_protected_manual_records(project_terms):
    manager = TermDatabaseManager(project_terms.config, "legacy.esp")
    manager.load_all()
    manager.get_dynamic_db().add_many_and_save([("Sword", "手动剑", "manual", "")])
    writer = GeneratedTermWriter(manager)
    assert writer.save([("Sword", "自动剑", "auto_name", "")]) == 0
    assert writer.save([("Shield", "盾", "auto_name", ""), ("", "无效", "auto_name", "")]) == 1


def test_independent_pools_cannot_overwrite_each_other_between_validation_and_save(project_terms, monkeypatch):
    first = project_terms.pool
    second = SharedTermPool("project", "variant", "zh_CN", path=project_terms.path.parent / "." / "shared.json")
    saving, release = threading.Event(), threading.Event()
    persist = first._write_bytes

    def pause_before_persist(payload):
        saving.set()
        assert release.wait(5), "test did not release the first pool's write"
        persist(payload)

    monkeypatch.setattr(first, "_write_bytes", pause_before_persist)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_write = executor.submit(first.publish, [("Sword", "剑", "auto_name", "")])
        try:
            assert saving.wait(3)
            second_write = executor.submit(second.publish, [("Shield", "盾", "auto_name", "")])
            with pytest.raises(TermPublicationConflict) as failure:
                second_write.result(timeout=3)
            assert isinstance(failure.value.__cause__, ReadOnlyWriteRefused)
            assert failure.value.__cause__.code == "SHARED_TERMINOLOGY_BUSY"
        finally:
            release.set()
        assert first_write.result(timeout=5) == [("Sword", "剑", "auto_name", "")]

    assert second.snapshot().entries == ()
    with pytest.raises(RuntimeError, match="其他任务修改"):
        second.load()
    with pytest.raises(RuntimeError, match="其他任务修改"):
        second.publish([("Shield", "盾", "auto_name", "")])
    restored = SharedTermPool("project", "variant", "zh_CN", path=project_terms.path)
    restored.publish([("Shield", "盾", "auto_name", "")])
    assert {entry.term for entry in restored.as_list()} == {"Sword", "Shield"}


@pytest.mark.parametrize("reject_acknowledgement", [False, True])
def test_initial_reader_cannot_restore_a_write_that_is_waiting_for_acknowledgement(
    project_terms, reject_acknowledgement
):
    waiting, release = threading.Event(), threading.Event()

    def acknowledge(_terms):
        # Same-object reentrant reads remain available to TaskConsistency.
        project_terms.pool.load()
        waiting.set()
        assert release.wait(5), "test did not release acknowledgement"
        if reject_acknowledgement:
            raise RuntimeError("acknowledgement rejected")

    with ThreadPoolExecutor(max_workers=2) as executor:
        writing = executor.submit(
            project_terms.pool.publish, [("Sword", "剑", "auto_name", "")], acknowledge=acknowledge
        )
        try:
            assert waiting.wait(3)
            reading = executor.submit(SharedTermPool, "project", "variant", "zh_CN", path=project_terms.path)
            with pytest.raises(ReadOnlyWriteRefused) as failure:
                reading.result(timeout=3)
            assert failure.value.code == "SHARED_TERMINOLOGY_BUSY"
        finally:
            release.set()
        if reject_acknowledgement:
            with pytest.raises(RuntimeError, match="acknowledgement rejected"):
                writing.result(timeout=5)
        else:
            writing.result(timeout=5)

    restored = SharedTermPool("project", "variant", "zh_CN", path=project_terms.path)
    assert len(restored.snapshot().entries) == (0 if reject_acknowledgement else 1)
