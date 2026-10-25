from types import SimpleNamespace

from transbridge.ai_translator.name_preparation_authority import SourceNameAuthority
from transbridge.ai_translator.project_terminology_adapter import (
    EnabledEffectiveTerminologyGate,
    ProjectTerminologyAdapter,
)
from transbridge.ai_translator.required_term_matching import MandatoryTermMatcher
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    TerminologyLookupContext,
)
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.translation.terminology_run_snapshot import (
    FrozenEffectiveTerminologyPort,
    TerminologyRunSnapshotFactory,
)
from transbridge.converter.translation_entry import TranslationEntry


def _entry(key, original="Iron Sword", *, plugin="A.esp"):
    return TranslationEntry(key, key, original, "", 0, "WEAP:FULL", form_id_with_plugin=f"000001|{plugin}")


def _decision(key, original, text, *, plugin=None, suppressed=False):
    return TermDecision(
        key,
        "project",
        "variant",
        original,
        original.casefold(),
        text,
        scope=TermScope.project() if plugin is None else TermScope.plugin(plugin),
        status=DecisionStatus.ADOPTED,
        suppressed=suppressed,
    )


class _Manager:
    def __init__(self, loader=None):
        self._effective_loader = loader
        self.loads = []

    def lookup_context_for_entry(self, entry):
        return TerminologyLookupContext("project", "variant", entry.form_id_with_plugin.split("|")[1])

    def _effective_terms(self, context):
        self.loads.append(context)
        legacy = [TermEntry("Sword", "旧剑", "legacy"), TermEntry("Blocked", "不应采用", "legacy")]
        return legacy if self._effective_loader is None else self._effective_loader.load(context, legacy).entries


def test_optional_subterm_matching_is_lazy_and_cache_distinguishes_the_flag(monkeypatch):
    manager = _Manager()
    authority = SourceNameAuthority(manager, [], excluded=set())
    calls = []
    original = MandatoryTermMatcher.from_terms

    def build(entries):
        calls.append(None)
        return original(entries)

    monkeypatch.setattr(MandatoryTermMatcher, "from_terms", build)
    entry = _entry("entry")
    assert authority.for_entry(entry, check_required_terms=False).required_terms == ()
    assert calls == []
    assert authority.for_entry(entry).required_terms == (("Sword", "旧剑"),)
    assert authority.for_entry(entry, check_required_terms=False).required_terms == ()
    assert authority.for_entry(_entry("other", "Steel Sword")).required_terms == (("Sword", "旧剑"),)
    assert len(calls) == len(manager.loads) == 1


def test_published_snapshot_is_validated_per_scope_and_never_resolved_per_name(monkeypatch):
    snapshot = EffectiveTerminologySnapshot(
        "project",
        "variant",
        EffectiveSnapshotStatus.READY,
        "version",
        "digest",
        (
            _decision("sword", "Sword", "剑"),
            _decision("plugin", "Sword", "刀", plugin="B.esp"),
            _decision("blocked", "Blocked", "废止", suppressed=True),
        ),
    )
    frozen = TerminologyRunSnapshotFactory(SimpleNamespace(snapshot=lambda *_: snapshot)).freeze("project", "variant")
    port = FrozenEffectiveTerminologyPort(frozen)
    loader = ProjectTerminologyAdapter(port, EnabledEffectiveTerminologyGate())
    snapshots = []
    read_snapshot = port.snapshot

    def counted(*args):
        snapshots.append(args)
        return read_snapshot(*args)

    def unexpected(*_):
        raise AssertionError("per-name resolution must reuse the verified scope index")

    monkeypatch.setattr(port, "snapshot", counted)
    monkeypatch.setattr(loader, "resolve", unexpected)
    manager = _Manager(loader)
    authority = SourceNameAuthority(manager, [], excluded=set())
    for index in range(10):
        result = authority.for_entry(_entry(str(index), f"Name {index} Sword"))
        assert result.required_terms == (("Sword", "剑"),)
    assert authority.for_entry(_entry("blocked", "Blocked"), check_required_terms=False).blocked
    assert authority.for_entry(_entry("direct", "Sword"), check_required_terms=False).exact_translation == "剑"
    assert len(snapshots) == 2
    assert authority.for_entry(_entry("plugin", "Sword", plugin="B.esp")).exact_translation == "刀"
    assert len(snapshots) == 4
    assert len(manager.loads) == 2


def test_disabled_published_gate_keeps_legacy_without_snapshot_reads(monkeypatch):
    loader = ProjectTerminologyAdapter(SimpleNamespace())
    manager = _Manager(loader)

    def unexpected(*_):
        raise AssertionError("disabled published terminology must not read a snapshot")

    monkeypatch.setattr(loader, "effective_snapshot", unexpected)
    authority = SourceNameAuthority(manager, [], excluded=set())
    result = authority.for_entry(_entry("entry", "Sword"), check_required_terms=False)
    assert result.exact_translation == "旧剑"
    assert not result.blocked
