from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, Lock, Thread, current_thread
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.project_terminology_adapter import (
    EnabledEffectiveTerminologyGate,
    ProjectTerminologyAdapter,
)
from transbridge.ai_translator.required_term_matching import MandatoryTermMatcher
from transbridge.ai_translator.shared_term_lookup import shared_terms_for_entry
from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry, dump_terms_json
from transbridge.ai_translator.term_lookup_cache import TermLookupCache
from transbridge.ai_translator.translation_request import translation_request
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    SnapshotEffectiveTerminologyPort,
    TerminologyLookupContext,
)
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.config.llm import LLMConfig


@pytest.fixture
def setup_terms(tmp_path, monkeypatch):
    def directory(stem):
        folder = tmp_path / "legacy" / stem
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(directory))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    pool = SharedTermPool("project", "variant", config.target_lang, path=tmp_path / "shared.json")

    def manager(*, shared=True, adapter=None):
        result = TermDatabaseManager(
            config,
            "source.esp",
            shared_term_pool=pool if shared else None,
            effective_loader=adapter,
            terminology_context=TerminologyLookupContext("project", "variant") if shared or adapter else None,
        )
        result.load_all()
        return result

    return SimpleNamespace(manager=manager, pool=pool, config=config)


def entry(text, plugin="one.esp"):
    return SimpleNamespace(key=text, original=text, terminology_plugin_id=plugin)


def decision(original, translation, *, plugin=None, suppressed=False):
    scope = TermScope.project() if plugin is None else TermScope.plugin(plugin)
    return TermDecision(
        term_id(project_id="project", variant_id="variant", scope=scope, original=original),
        "project",
        "variant",
        original,
        normalize_original(original),
        translation,
        scope=scope,
        status=DecisionStatus.ADOPTED,
        suppressed=suppressed,
    )


def adapter_for(*decisions):
    source = SimpleNamespace(
        value=EffectiveTerminologySnapshot(
            "project",
            "variant",
            EffectiveSnapshotStatus.READY,
            "v1",
            "digest1",
            decisions,
        )
    )
    source.snapshot = lambda *_: source.value
    return ProjectTerminologyAdapter(
        SnapshotEffectiveTerminologyPort(source), EnabledEffectiveTerminologyGate()
    ), source


def test_repeated_entries_share_one_view_and_matcher_but_keep_their_own_matches(setup_terms, monkeypatch):
    setup_terms.pool.publish([("White River", "白河", "auto", ""), ("White", "白色", "auto", "")])
    manager = setup_terms.manager()
    builds = []
    original = MandatoryTermMatcher.__init__

    def track(self, forms):
        builds.append(tuple(forms))
        original(self, forms)

    monkeypatch.setattr(MandatoryTermMatcher, "__init__", track)
    assert manager.match_terms_for_entry(entry("White River")) == {"White River": "白河"}
    assert manager.match_terms_for_entry(entry("White")) == {"White": "白色"}
    assert manager.match_terms_for_entry(entry("Whitefish")) == {}
    assert len(builds) == 1


def test_alias_case_and_primary_priority_survive_view_reuse(setup_terms):
    manager = setup_terms.manager(shared=False)
    database = manager.get_dynamic_db()
    dump_terms_json(
        database._path,
        [
            TermEntry("Dawn", "黎明", "manual", case_sensitive=True, variants=["Morning"]),
            TermEntry("Morning", "早晨", "manual"),
            TermEntry("White", "白色", "manual"),
            TermEntry("White River", "白河", "manual", variants=["Pale River"]),
        ],
    )
    database.load()
    manager.load_all()
    for _ in range(2):
        assert manager.match_terms_for_entry(entry("Dawn dawn Morning White River White Pale River")) == {
            "Dawn": "黎明",
            "Morning": "早晨",
            "White River": "白河",
            "White": "白色",
        }
        assert manager.match_terms_for_entry(entry("dawn")) == {}


@pytest.mark.parametrize("lookup", ["normal", "shared", "exact"])
def test_pool_revision_and_plugin_scope_are_isolated(setup_terms, lookup):
    manager = setup_terms.manager()
    read = {
        "normal": manager.match_terms_for_entry,
        "shared": lambda row: shared_terms_for_entry(manager, row),
        "exact": lambda row: manager.exact_match([row.original], context=manager.lookup_context_for_entry(row)),
    }[lookup]
    setup_terms.pool.publish([("Dawn", "黎明", "auto", "")])
    assert read(entry("Dawn", "a.esp")) == {"Dawn": "黎明"}
    setup_terms.pool.publish([("Dawn", "晨光", "auto", "")], plugin_id="a.esp")
    assert read(entry("Dawn", "a.esp")) == {"Dawn": "晨光"}
    assert read(entry("Dawn", "b.esp")) == {"Dawn": "黎明"}
    setup_terms.pool.publish([("NewCity", "新城", "auto", "")])
    assert read(entry("NewCity", "a.esp")) == {"NewCity": "新城"}


def test_dynamic_add_and_reload_invalidate_existing_views(setup_terms):
    manager = setup_terms.manager(shared=False)
    database = manager.get_dynamic_db()
    database.add_many_and_save([("Sword", "旧剑", "auto", "")])
    manager.load_all()
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "旧剑"}
    database.add("Sword", "新剑", "auto")
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "新剑"}
    dump_terms_json(database._path, [TermEntry("Sword", "手动剑", "manual")])
    database.load()
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "手动剑"}
    database.add("Shield", "盾", "auto")
    assert manager.match_terms_for_entry(entry("Shield")) == {"Shield": "盾"}


def test_load_all_rebuilds_legacy_source_precedence(setup_terms, tmp_path):
    manager = setup_terms.manager()
    setup_terms.pool.publish([("Sword", "共享剑", "auto", "")])
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "共享剑"}
    local = tmp_path / "terms.json"
    dump_terms_json(local, [TermEntry("Sword", "自定剑", "json")])
    setup_terms.config.local_json_path = str(local)
    setup_terms.config.term_priority = ["json", "dynamic"]
    manager.load_all()
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "自定剑"}
    assert shared_terms_for_entry(manager, entry("Sword")) == {"Sword": "自定剑"}


def test_published_revision_and_suppression_cannot_reuse_old_matches(setup_terms):
    adapter, source = adapter_for(decision("Sword", "项目剑"))
    manager = setup_terms.manager(adapter=adapter)
    setup_terms.pool.publish([("Sword", "共享剑", "auto", "")])
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "项目剑"}
    source.value = replace(
        source.value,
        version_id="v2",
        content_digest="digest2",
        decisions=(
            decision("Sword", "项目刀"),
            decision("Sword", "插件刀", plugin="one.esp"),
        ),
    )
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "插件刀"}
    assert manager.match_terms_for_entry(entry("Sword", "other.esp")) == {"Sword": "项目刀"}
    source.value = replace(
        source.value, version_id="v3", content_digest="digest3", decisions=(decision("Sword", "", suppressed=True),)
    )
    assert manager.match_terms_for_entry(entry("Sword")) == {}
    assert shared_terms_for_entry(manager, entry("Sword")) == {}


def test_supplied_snapshot_stays_old_while_new_admissions_see_latest(setup_terms):
    manager = setup_terms.manager()
    setup_terms.pool.publish([("Sword", "剑", "auto", "")])
    captured = setup_terms.pool.snapshot("one.esp")
    assert shared_terms_for_entry(manager, entry("Sword"), snapshot=captured) == {"Sword": "剑"}
    setup_terms.pool.publish([("Shield", "盾", "auto", "")])
    assert shared_terms_for_entry(manager, entry("Shield"), snapshot=captured) == {}
    assert shared_terms_for_entry(manager, entry("Shield")) == {"Shield": "盾"}


def test_supplied_snapshot_does_not_cache_published_resolution(setup_terms):
    adapter, source = adapter_for(decision("Sword", "项目剑"))
    manager = setup_terms.manager(adapter=adapter)
    setup_terms.pool.publish([("Sword", "共享剑", "auto", "")])
    captured = setup_terms.pool.snapshot("one.esp")
    assert shared_terms_for_entry(manager, entry("Sword"), snapshot=captured) == {"Sword": "项目剑"}
    source.value = replace(
        source.value, version_id="v2", content_digest="digest2", decisions=(decision("Sword", "", suppressed=True),)
    )
    assert shared_terms_for_entry(manager, entry("Sword"), snapshot=captured) == {}


def test_unversioned_mutable_providers_keep_live_lookup(setup_terms):
    manager = setup_terms.manager(shared=False)
    values = [TermEntry("Sword", "旧剑", "dynamic")]
    manager._dynamic_db = SimpleNamespace(as_list=lambda: values)
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "旧剑"}
    assert manager.exact_match(["Sword"]) == {"Sword": "旧剑"}
    values[0].translation = "新剑"
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "新剑"}
    assert manager.exact_match(["Sword"]) == {"Sword": "新剑"}


def test_unversioned_provider_owns_its_nonreentrant_lock(setup_terms):
    manager = setup_terms.manager(shared=False)
    lock = Lock()

    def values():
        assert lock.acquire(blocking=False), "lookup must not pre-acquire a foreign provider's private lock"
        try:
            return [TermEntry("Sword", "剑", "dynamic")]
        finally:
            lock.release()

    manager._dynamic_db = SimpleNamespace(_lock=lock, as_list=values)
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "剑"}


def test_disabled_retrieval_bypasses_cached_views(setup_terms):
    manager = setup_terms.manager()
    setup_terms.pool.publish([("Sword", "剑", "auto", "")])
    assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "剑"}
    manager._retrieval_enabled = False
    assert manager.match_terms_for_entry(entry("Sword")) == {}
    assert shared_terms_for_entry(manager, entry("Sword")) == {}


def test_scope_cache_is_bounded_and_evicted_scopes_are_correct(setup_terms):
    manager = setup_terms.manager()
    manager._lookup_cache = TermLookupCache(capacity=2)
    for plugin, target in (("a", "甲"), ("b", "乙"), ("c", "丙")):
        setup_terms.pool.publish([("Sword", target, "auto", "")], plugin_id=plugin)
    for plugin, target in (("a", "甲"), ("b", "乙"), ("c", "丙"), ("a", "甲")):
        assert manager.match_terms_for_entry(entry("Sword", plugin)) == {"Sword": target}
        assert shared_terms_for_entry(manager, entry("Sword", plugin)) == {"Sword": target}
    assert len(manager._lookup_cache._views) == 2
    assert len(manager._lookup_cache._shared) == 2


def test_simultaneous_first_lookups_publish_one_immutable_matcher(setup_terms, monkeypatch):
    manager = setup_terms.manager()
    setup_terms.pool.publish([("Sword", "剑", "auto", "")])
    start = Barrier(4)
    builds = []
    original = MandatoryTermMatcher.__init__

    def track(self, forms):
        builds.append(tuple(forms))
        original(self, forms)

    def read(_):
        start.wait(timeout=5)
        return manager.match_terms_for_entry(entry("Sword"))

    monkeypatch.setattr(MandatoryTermMatcher, "__init__", track)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read, range(4))) == [{"Sword": "剑"}] * 4
    assert len(builds) == 1


def test_publication_callback_can_lookup_while_another_reader_waits(setup_terms, monkeypatch):
    manager = setup_terms.manager()
    setup_terms.pool.publish([("Sword", "剑", "auto", "")])
    attempted = Event()
    pool_lock = setup_terms.pool._lock
    cache_lock = manager._lookup_cache._lock
    results, errors = [], []

    class ObservedPoolLock:
        def __enter__(self):
            if current_thread().name == "lookup-contender":
                attempted.set()
            pool_lock.acquire()

        def __exit__(self, *_):
            pool_lock.release()

    class BoundedCacheLock:
        def __enter__(self):
            assert cache_lock.acquire(timeout=2), "publication callback and lookup reversed their locks"

        def __exit__(self, *_):
            cache_lock.release()

    def read():
        try:
            results.append(manager.match_terms_for_entry(entry("Sword")))
        except Exception as exc:
            errors.append(exc)

    contender = Thread(target=read, name="lookup-contender", daemon=True)

    def acknowledge(_):
        contender.start()
        assert attempted.wait(2)
        assert manager.match_terms_for_entry(entry("Sword")) == {"Sword": "剑"}

    monkeypatch.setattr(setup_terms.pool, "_lock", ObservedPoolLock())
    monkeypatch.setattr(manager._lookup_cache, "_lock", BoundedCacheLock())
    try:
        setup_terms.pool.publish([("Shield", "盾", "auto", "")], acknowledge=acknowledge)
    finally:
        if contender.ident is not None:
            contender.join(timeout=3)
    assert not contender.is_alive()
    assert not errors
    assert results == [{"Sword": "剑"}]


@pytest.mark.parametrize("lookup", ["normal", "shared"])
def test_failed_publication_cannot_poison_a_reused_revision(setup_terms, lookup):
    manager = setup_terms.manager()
    read = manager.match_terms_for_entry if lookup == "normal" else lambda row: shared_terms_for_entry(manager, row)

    def reject(_):
        assert read(entry("AbandonedCity")) == {"AbandonedCity": "废弃城"}
        raise RuntimeError("reject acknowledgement")

    with pytest.raises(RuntimeError, match="reject acknowledgement"):
        setup_terms.pool.publish([("AbandonedCity", "废弃城", "auto", "")], acknowledge=reject)
    assert setup_terms.pool.revision == 0
    setup_terms.pool.publish([("CommittedCity", "已提交城", "auto", "")])
    assert setup_terms.pool.revision == 1
    assert read(entry("AbandonedCity CommittedCity")) == {"CommittedCity": "已提交城"}


def test_exact_index_reuses_immutable_values_until_dynamic_revision_changes(setup_terms, monkeypatch):
    manager = setup_terms.manager(shared=False)
    database = manager.get_dynamic_db()
    database.add("Sword", "旧剑", "auto")
    assert manager.exact_match(["Sword"]) == {"Sword": "旧剑"}
    effective = manager._effective_terms

    def unexpected_scan(*_):
        pytest.fail("unchanged exact lookups must reuse the compiled scope")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_effective_terms", unexpected_scan)
        for original in ("Sword", " SWORD ", "Ｓｗｏｒｄ"):
            assert manager.exact_match([original]) == {"Sword": "旧剑"}
    database.add("Sword", "新剑", "auto")
    assert manager.exact_match(["Sword"]) == {"Sword": "新剑"}
    assert effective()[0].translation == "新剑"


def test_exact_index_preserves_alias_last_wins_separately_from_mandatory_forms(setup_terms):
    manager = setup_terms.manager(shared=False)
    database = manager.get_dynamic_db()
    dump_terms_json(
        database._path,
        [
            TermEntry("Blade", "刀", "manual"),
            TermEntry("Sword", "剑", "manual", variants=["Blade"]),
            TermEntry("US", "美国", "manual", case_sensitive=True),
            TermEntry("us", "我们", "manual"),
        ],
    )
    database.load()
    manager.load_all()
    for _ in range(2):
        assert manager.match_terms_for_entry(entry("Blade")) == {"Blade": "刀"}
        assert manager.exact_match(["ＢＬＡＤＥ", "US", "us"]) == {"Sword": "剑", "US": "美国", "us": "我们"}


def test_exact_index_cannot_reuse_a_rejected_transaction_with_the_same_revision(setup_terms):
    manager = setup_terms.manager()

    def reject(_):
        assert manager.exact_match(["Sword"]) == {"Sword": "剑"}
        raise RuntimeError("reject")

    with pytest.raises(RuntimeError, match="reject"):
        setup_terms.pool.publish([("Sword", "剑", "auto", "")], acknowledge=reject)
    setup_terms.pool.publish([("Shield", "盾", "auto", "")])
    assert manager.exact_match(["Sword", "Shield"]) == {"Shield": "盾"}


def test_new_batch_captures_reuse_snapshot_and_matcher_without_scanning_records(setup_terms, monkeypatch):
    manager = setup_terms.manager()
    setup_terms.pool.publish([("Sword", "剑", "auto", ""), ("Shield", "盾", "auto", "")])
    snapshots = []
    snapshot_method = setup_terms.pool.snapshot
    built = []

    def build(entries, _flat, _batch_type, *, terms_by_entry):
        built.append(terms_by_entry)
        return []

    def capture(plugin=None):
        result = snapshot_method(plugin)
        snapshots.append(result)
        return result

    def finalize(original):
        request = translation_request(
            manager=manager,
            builder=SimpleNamespace(build_translation_prompt=build),
            entries=[entry(original)],
            batch_type="dialogue",
            config=setup_terms.config,
            shared_terms={},
            terms_lock=Lock(),
            required_terms={},
            effective_required={},
            log=lambda _: None,
        )
        request.finalize(SimpleNamespace(terms_by_entry={}))

    monkeypatch.setattr(setup_terms.pool, "snapshot", capture)
    finalize("Sword")

    def unexpected_rebuild(*_):
        pytest.fail("new batches at the same revision must reuse frozen records and their matcher")

    with monkeypatch.context() as patch:
        patch.setattr(MandatoryTermMatcher, "__init__", unexpected_rebuild)
        patch.setattr("transbridge.ai_translator.shared_term_snapshot.normalize_original", unexpected_rebuild)
        patch.setattr("transbridge.ai_translator.shared_term_snapshot.FrozenTermEntry.mutable_copy", unexpected_rebuild)
        finalize("Shield")
        finalize("Sword and Shield")
    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    assert built == [
        {"Sword": {"Sword": "剑"}},
        {"Shield": {"Shield": "盾"}},
        {"Sword and Shield": {"Sword": "剑", "Shield": "盾"}},
    ]
    setup_terms.pool.publish([("Bow", "弓", "auto", "")])
    finalize("Bow")
    assert snapshots[-1] is not snapshots[0]
    assert built[-1] == {"Bow": {"Bow": "弓"}}


def test_slow_source_reload_does_not_hold_the_other_managers_project_pool(setup_terms, monkeypatch):
    reader = setup_terms.manager()
    loading = setup_terms.manager()
    setup_terms.pool.publish([("Sword", "剑", "auto", "")])
    started, release = Event(), Event()

    def remote_source():
        started.set()
        assert release.wait(3), "test did not release the synthetic remote source"
        return []

    def read_committed():
        return reader.match_terms_for_entry(entry("Sword")), shared_terms_for_entry(reader, entry("Sword"))

    setup_terms.config.term_priority = ["paratranz"]
    monkeypatch.setattr(loading, "_load_paratranz", remote_source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reload_future = pool.submit(loading.load_all)
        try:
            assert started.wait(2)
            assert pool.submit(read_committed).result(timeout=2) == ({"Sword": "剑"}, {"Sword": "剑"})
        finally:
            release.set()
        reload_future.result(timeout=3)


def test_publication_callback_fails_closed_instead_of_waiting_for_reload(setup_terms, monkeypatch):
    manager = setup_terms.manager()
    started, release = Event(), Event()

    def remote_source():
        started.set()
        assert release.wait(3)
        return []

    def forbidden_wait(*_):
        raise AssertionError("a pool owner must never wait for a reloader that may need the pool")

    setup_terms.config.term_priority = ["paratranz"]
    monkeypatch.setattr(manager, "_load_paratranz", remote_source)
    monkeypatch.setattr(manager._lookup_cache._ready, "wait", forbidden_wait)
    with ThreadPoolExecutor(max_workers=1) as workers:
        futures = []

        def acknowledge(_):
            futures.append(workers.submit(manager.load_all))
            assert started.wait(2)
            try:
                manager.match_terms_for_entry(entry("Sword"))
            finally:
                release.set()

        with pytest.raises(RuntimeError, match="术语来源正在重新加载"):
            setup_terms.pool.publish([("Sword", "剑", "auto", "")], acknowledge=acknowledge)
        futures[0].result(timeout=3)
    assert setup_terms.pool.snapshot().entries == ()
