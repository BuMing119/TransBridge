import csv
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.term_formats import load_terms_csv, load_terms_json
from transbridge.ai_translator.term_source_reader import _source_entry
from transbridge.application.terminology.models import DecisionStatus, ScopeKind, TermDecision, TermScope
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.infra.terminology_export import TerminologyExport, conflict_export, write_terminology_export


def decision(index=0):
    return TermDecision(
        f"term-{index}",
        "project",
        "variant",
        f"Dragon {index}",
        f"dragon {index}",
        f"巨龙 {index}",
        scope=TermScope(ScopeKind.PLUGIN, "plugin.esp"),
        status=DecisionStatus.ADOPTED,
        variants=("Dragon variant",),
        notes="人工备注",
    )


@pytest.mark.parametrize("suffix,loader", [(".json", load_terms_json), (".csv", load_terms_csv)])
def test_complete_terms_roundtrip_retains_scope_and_every_row(tmp_path, suffix, loader):
    path = tmp_path / f"terms{suffix}"
    snapshot = TerminologyExport("terms", tuple(decision(i) for i in range(305)))
    assert write_terminology_export(path, snapshot) == 305
    rows = loader(path)
    assert len(rows) == 305
    assert rows[-1].translation == "巨龙 304"
    assert rows[0].variants == ["Dragon variant"] and rows[0].note == "人工备注"
    restored = _source_entry(rows[0])
    assert restored.plugin_id == "plugin.esp"


def conflict_snapshot():
    variants = tuple(
        SimpleNamespace(normalized_translation=name, evidence_ids=(f"e-{i}",)) for i, name in enumerate(("龙", "巨龙"))
    )
    conflict = SimpleNamespace(
        conflict_group_id="c", normalized_original="dragon", variants=variants, risk="high", status="unresolved"
    )
    evidence = tuple(
        SimpleNamespace(
            evidence_id=f"e-{i}",
            original="Dragon",
            translation=name,
            plugin_scope=f"p{i}.esp",
            source_chain=(f"source-{i}",),
            entry_key=f"key-{i}",
            context=f"context-{i}",
        )
        for i, name in enumerate(("龙", "巨龙"))
    )
    resolved = SimpleNamespace(**{**vars(conflict), "status": "unified"})
    build = SimpleNamespace(ref="build", conflicts=(conflict,), evidence=evidence)
    draft = SimpleNamespace(
        conflict_resolutions=(resolved,), decisions=(replace(decision(), normalized_original="dragon"),)
    )
    return conflict_export(build, draft)


@pytest.mark.parametrize("suffix", [".json", ".csv"])
def test_conflict_file_contains_candidates_evidence_and_current_resolution(tmp_path, suffix):
    path = tmp_path / f"conflicts{suffix}"
    assert write_terminology_export(path, conflict_snapshot()) == 1
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = json.load(stream) if suffix == ".json" else list(csv.DictReader(stream))
    assert {row["候选译名"] for row in rows} == {"龙", "巨龙"}
    assert [row["上下文"] for row in rows] == ["context-0", "context-1"]
    assert all(row["处理状态"] == "已统一" for row in rows)
    assert rows[0]["当前采用译名"] == "plugin.esp：巨龙 0"


def test_failed_export_retains_existing_file_and_removes_staging(tmp_path, monkeypatch):
    path = tmp_path / "terms.json"
    path.write_text("keep existing", encoding="utf-8")

    def fail(staged, entries):
        staged.write_text("partial", encoding="utf-8")
        raise OSError("disk write failed")

    monkeypatch.setattr("transbridge.infra.terminology_export.dump_terms_json", fail)
    with pytest.raises(OSError, match="disk write failed"):
        write_terminology_export(path, TerminologyExport("terms", (decision(),)))
    assert path.read_text(encoding="utf-8") == "keep existing"
    assert list(tmp_path.iterdir()) == [path]


def test_cancelled_export_never_replaces_target(tmp_path):
    path = tmp_path / "terms.json"
    path.write_text("keep existing", encoding="utf-8")
    token = QueryCancellation()
    token.cancel()
    with pytest.raises(QueryCancelled):
        write_terminology_export(path, TerminologyExport("terms", (decision(),)), cancellation=token)
    assert path.read_text(encoding="utf-8") == "keep existing"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("same_build", [True, False])
def test_published_conflict_status_is_retained_only_for_matching_build(same_build):
    snapshot = conflict_snapshot()
    original = SimpleNamespace(**{**vars(snapshot.items[0]), "status": "unresolved"})
    build = SimpleNamespace(ref="build", conflicts=(original,), evidence=snapshot.evidence)
    version = SimpleNamespace(
        build_ref="build" if same_build else "old-build", conflicts=snapshot.items, decisions=snapshot.decisions
    )
    result = conflict_export(build, None, version)
    assert result.items[0].status == ("unified" if same_build else "unresolved")
    assert result.decisions == (snapshot.decisions if same_build else ())


@pytest.mark.parametrize("suffix", [".json", ".csv"])
def test_summary_is_one_record_per_conflict_with_usage_counts_and_minor_evidence(tmp_path, suffix):
    snapshot = replace(conflict_snapshot(), kind="conflict_summary")
    path = tmp_path / f"summary{suffix}"
    write_terminology_export(path, snapshot)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = json.load(stream) if suffix == ".json" else list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["冲突编号"] == "c"
    assert rows[0]["候选译名及使用词条数"] == "龙：1；巨龙：1"
    assert rows[0]["少数译名"] == "龙；巨龙"
    assert "Dragon" in rows[0]["少数译名代表证据"]


@pytest.mark.parametrize("kind", ["conflicts", "conflict_summary"])
@pytest.mark.parametrize("suffix", [".json", ".csv"])
def test_judgment_reason_and_canonical_translation_are_exported(tmp_path, kind, suffix):
    snapshot = conflict_snapshot()
    conflict = SimpleNamespace(**{
        **vars(snapshot.items[0]),
        "status": "equivalent",
        "recommended_translation": "龙",
        "notes": "LLM 判定：同一术语的正常表达差异",
    })
    path = tmp_path / f"judgment{suffix}"
    write_terminology_export(path, replace(snapshot, kind=kind, items=(conflict,)))
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = json.load(stream) if suffix == ".json" else list(csv.DictReader(stream))
    assert all(row["处理状态"] == "同一术语 · 无需处理" for row in rows)
    assert all(row["归并译名"] == "龙" for row in rows)
    assert all(row["判断说明"] == conflict.notes for row in rows)
    if kind == "conflicts":
        assert {row["证据译文"] for row in rows} == {"龙", "巨龙"}
