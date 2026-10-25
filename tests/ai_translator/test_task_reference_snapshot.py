"""Task reference reuse across stages, sources, retry and process recovery."""

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.project_reference_snapshot import capture_project_reference, restore_project_reference
from transbridge.ai_translator.project_terminology_runtime import freeze_project_terminology
from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.task_reference_snapshot import TaskReferenceSnapshotError, TaskReferenceStore
from transbridge.ai_translator.term_database import DynamicTermDatabase, TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry, dump_terms_json
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.config.llm import LLMConfig


@pytest.fixture
def managers(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda stem: str(tmp_path / stem)))
    config = LLMConfig(term_priority=["paratranz", "dynamic"], enable_semantic_match=False)
    terms = {"Sword": "剑"}
    calls = []

    def remote(*args, **kwargs):
        calls.append((args, kwargs))
        return {"results": [{"term": term, "translation": text} for term, text in terms.items()]}

    pool = SharedTermPool("project", "variant", config.target_lang, path=tmp_path / "pool.json")

    def create(store, source="A"):
        return TermDatabaseManager(
            config,
            f"{source}.esp",
            SimpleNamespace(list_terms=remote),
            123,
            shared_term_pool=pool,
            reference_store=store,
            reference_source=source,
        )

    return SimpleNamespace(create=create, terms=terms, calls=calls, pool=pool)


def test_repeated_stages_and_sources_reuse_baseline_while_generated_terms_remain_live(managers):
    store = TaskReferenceStore("task")
    initial = managers.create(store)
    assert initial.load_all() == {"Sword": "剑"}
    managers.terms["Sword"] = "不应进入当前任务"
    managers.pool.publish([("Whiterun", "雪漫", "auto", "")])
    for source in ("A", "B", "A"):
        current = managers.create(store, source)
        assert current.load_all() == {"Sword": "剑", "Whiterun": "雪漫"}
    assert len(managers.calls) == 1
    # A genuinely new task sees the latest external reference.
    assert managers.create(TaskReferenceStore("new-task")).load_all()["Sword"] == "不应进入当前任务"


def test_recovery_loads_complete_baseline_without_contacting_remote(managers, tmp_path):
    store = TaskReferenceStore("task", root=tmp_path / "references", identity=("p", "v", "zh"))
    managers.create(store).load_all()
    restored = TaskReferenceStore("task", root=tmp_path / "references", identity=("p", "v", "zh"))
    managers.terms.clear()
    assert restored.restored
    assert managers.create(restored).load_all() == {"Sword": "剑"}
    assert len(managers.calls) == 1
    with pytest.raises(ValueError, match="身份不匹配"):
        TaskReferenceStore("task", root=tmp_path / "references", identity=("another", "v", "zh"))


@pytest.mark.parametrize("cached", [True, False])
def test_first_remote_failure_freezes_cache_or_empty_reference_for_every_source(managers, tmp_path, cached):
    store = TaskReferenceStore("task", root=tmp_path / "references")
    first = managers.create(store, "A")
    second = managers.create(store, "B")
    if cached:
        first._save_source_cache("paratranz", [TermEntry("Sword", "旧缓存", "paratranz")])
    second._save_source_cache("paratranz", [TermEntry("Sword", "另一个来源的缓存", "paratranz")])
    attempts = []

    def transient_remote(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("remote unavailable")
        return {"results": [{"term": "Sword", "translation": "后来恢复的远端"}]}

    first._paratranz_client = second._paratranz_client = SimpleNamespace(list_terms=transient_remote)
    expected = {"Sword": "旧缓存"} if cached else {}
    assert first.load_all() == expected
    assert second.load_all() == expected
    assert len(attempts) == 1
    diagnostic = "from cache (source failed: remote unavailable)" if cached else "remote unavailable"
    assert ("paratranz", len(expected), diagnostic) in first.get_load_log()
    assert ("paratranz", len(expected), diagnostic) in second.get_load_log()
    restored = TaskReferenceStore("task", root=tmp_path / "references")
    third = managers.create(restored, "C")
    third._paratranz_client = SimpleNamespace(list_terms=transient_remote)
    assert third.load_all() == expected
    assert ("paratranz", len(expected), diagnostic) in third.get_load_log()
    assert len(attempts) == 1


def test_snapshot_does_not_alias_manager_or_parallel_loader_inputs():
    store = TaskReferenceStore("task")
    entry = TermEntry("Sword", "剑", "json", metadata={"nested": [1]})
    calls = []

    def capture():
        calls.append(1)
        return [entry], [("json", 1, None)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.load_or_capture("A", capture), range(20)))
    entry.translation = "后来修改"
    entry.metadata["nested"].append(2)
    assert len(calls) == 1
    assert all(result is results[0] for result in results)
    assert results[0].records[0].translation == "剑"
    assert results[0].records[0].metadata["nested"] == (1,)


def test_corrupt_sidecar_is_preserved_and_never_silently_replaced(tmp_path):
    store = TaskReferenceStore("task", root=tmp_path)
    store.load_or_capture("A", lambda: ([TermEntry("Sword", "剑", "json")], ()))
    value = json.loads(store.path.read_text(encoding="utf-8"))
    value["payload"]["sources"]["A"]["entries"][0]["translation"] = "tampered"
    store.path.write_text(json.dumps(value), encoding="utf-8")
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="校验和不匹配"):
        TaskReferenceStore("task", root=tmp_path)
    assert store.path.read_bytes() == before


def test_recovered_native_term_keeps_all_fields_exactly(tmp_path):
    entry = TermEntry(
        " Sword ",
        " 剑 ",
        "paratranz",
        context=" context ",
        created_at="2026-10-24",
        case_sensitive=True,
        variants=[" Sword alias ", "a|b"],
        pos=" noun ",
        note=" note ",
        external_id="0123",
        metadata={"nested": [1, {"value": "kept"}]},
    )
    store = TaskReferenceStore("task", root=tmp_path)
    store.load_or_capture("A", lambda: ([entry], ()))
    restored = TaskReferenceStore("task", root=tmp_path)
    assert restored.load_or_capture("A", lambda: ([], ())).records[0].mutable_copy() == entry


def test_snapshot_save_failure_does_not_fall_back_to_source_cache(managers, tmp_path, monkeypatch):
    from transbridge.ai_translator import task_reference_snapshot

    managers.create(None).load_all()  # Existing source cache must not hide a failed task snapshot.

    def fail(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(task_reference_snapshot, "atomic_json", fail)
    store = TaskReferenceStore("task", root=tmp_path / "references")
    with pytest.raises(TaskReferenceSnapshotError, match="无法保存任务参考快照"):
        managers.create(store).load_all()
    assert not store.path.exists()


def test_failed_published_capture_can_retry_durable_write(tmp_path, monkeypatch):
    from transbridge.ai_translator import task_reference_snapshot

    original = task_reference_snapshot.atomic_json
    store = TaskReferenceStore("task", root=tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(task_reference_snapshot, "atomic_json", fail)
    with pytest.raises(TaskReferenceSnapshotError):
        store.capture_published({"snapshot": None})
    assert store.published is None
    monkeypatch.setattr(task_reference_snapshot, "atomic_json", original)
    store.capture_published({"snapshot": None})
    assert TaskReferenceStore("task", root=tmp_path).published == {"snapshot": None}


def test_nonshared_dynamic_view_keeps_own_terms_without_loading_external_additions(tmp_path, monkeypatch):
    from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda stem: str(tmp_path / stem)))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    (tmp_path / "A").mkdir()
    seed = DynamicTermDatabase("A.esp")
    seed.add_many_and_save([("Sword", "剑", "manual", "")])
    store = TaskReferenceStore("task", root=tmp_path / "references")

    def manager():
        value = TermDatabaseManager(config, "A.esp", reference_store=store, reference_source="A")
        value.load_all()
        return value

    first = manager()
    assert GeneratedTermWriter(first).save([("Whiterun", "雪漫", "auto", "")]) == 1
    dump_terms_json(seed._path, first.get_dynamic_db().as_list() + [TermEntry("External", "外部新增", "manual")])
    second = manager()
    assert second.get_dynamic_db() is first.get_dynamic_db()
    assert second.resolve_term("Whiterun").translation == "雪漫"
    assert second.resolve_term("External") is None
    restored = TaskReferenceStore("task", root=tmp_path / "references")
    after_restart = TermDatabaseManager(config, "A.esp", reference_store=restored, reference_source="A")
    after_restart.load_all()
    assert after_restart.resolve_term("External") is None


def test_published_reference_roundtrip_preserves_scoped_decisions_and_identity():
    decision = TermDecision("term", "p", "v", "Sword", "sword", "剑", TermScope.plugin("A.esp"), DecisionStatus.ADOPTED)
    snapshot = EffectiveTerminologySnapshot("p", "v", EffectiveSnapshotStatus.READY, "v1", "digest", (decision,))
    owner = SimpleNamespace(
        active_version_identity=("p", "v"),
        effective_terminology_factory=lambda *_: SimpleNamespace(effective_snapshot=lambda _: snapshot),
    )
    binding = freeze_project_terminology(owner)
    restored = restore_project_reference(json.loads(json.dumps(capture_project_reference(binding))))
    assert restored.snapshot_ref == binding.snapshot_ref
    assert restored.run_snapshot.decisions == binding.run_snapshot.decisions
    assert restored.adapter.resolve("Sword", restored.context.for_plugin("A.esp"), lambda _: None).translation == "剑"
    assert restored.adapter.resolve("Sword", restored.context.for_plugin("B.esp"), lambda _: None) is None
