from __future__ import annotations

import json
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    EetXmlAdapter,
    FormatId,
    LocalizedStringsAdapter,
    ParatranzJsonAdapter,
    ParseRequest,
    SourceDescriptor,
    SsePluginAdapter,
    XtXmlAdapter,
)

from .dataset import (
    REGULAR_DATASET,
    SMOKE_DATASET,
    STRESS_DATASET,
    canonical_dataset_digest,
    dataset_manifest,
    generate_terminology_dataset,
)


def test_fixed_scales_match_fr516_contract() -> None:
    assert (
        REGULAR_DATASET.source_count,
        REGULAR_DATASET.evidence_count,
        REGULAR_DATASET.terminology_count,
        REGULAR_DATASET.conflict_group_count,
        REGULAR_DATASET.version_count,
    ) == (50, 250_000, 50_000, 5_000, 10)
    assert (
        STRESS_DATASET.source_count,
        STRESS_DATASET.evidence_count,
        STRESS_DATASET.terminology_count,
        STRESS_DATASET.conflict_group_count,
        STRESS_DATASET.version_count,
    ) == (200, 1_000_000, 200_000, 20_000, 50)


def test_same_seed_produces_same_fingerprints_counts_and_digest(tmp_path: Path) -> None:
    first = generate_terminology_dataset(tmp_path / "first")
    second = generate_terminology_dataset(tmp_path / "second")

    first_manifest = dataset_manifest(first)
    second_manifest = dataset_manifest(second)

    assert first_manifest == second_manifest
    assert canonical_dataset_digest(first) == canonical_dataset_digest(second)
    assert first_manifest["expected_counts"] == {
        "sources": 5,
        "evidence": 64,
        "candidates": 20,
        "terminology": 16,
        "conflict_groups": 4,
        "versions": 3,
    }
    assert len({item["format_id"] for item in first_manifest["adapter_templates"]}) == 5


def test_generated_rows_have_exact_candidate_and_conflict_counts(tmp_path: Path) -> None:
    dataset = generate_terminology_dataset(tmp_path / "dataset")
    translations: dict[str, set[str]] = {}
    evidence_count = 0
    with dataset.evidence_file.open(encoding="utf-8") as stream:
        for line in stream:
            evidence_count += 1
            row = json.loads(line)
            translations.setdefault(row["original"], set()).add(row["translation"])

    assert evidence_count == SMOKE_DATASET.evidence_count
    assert len(translations) == SMOKE_DATASET.terminology_count
    assert sum(len(values) for values in translations.values()) == SMOKE_DATASET.candidate_count
    assert sum(len(values) > 1 for values in translations.values()) == SMOKE_DATASET.conflict_group_count


def test_real_adapter_templates_are_readable(tmp_path: Path) -> None:
    dataset = generate_terminology_dataset(tmp_path / "dataset")
    source_index = json.loads(dataset.source_index_file.read_text(encoding="utf-8"))
    adapters = {
        FormatId.PLUGIN_SSE: SsePluginAdapter(),
        FormatId.XML_EET: EetXmlAdapter(),
        FormatId.XML_XT: XtXmlAdapter(),
        FormatId.STRINGS: LocalizedStringsAdapter(FormatId.STRINGS),
        FormatId.JSON_PARATRANZ: ParatranzJsonAdapter(),
    }

    for template in source_index["templates"]:
        path = dataset.root / template["path"]
        format_id = FormatId(template["format_id"])
        result = adapters[format_id].parse(
            ParseRequest(
                SourceDescriptor(str(path), path.name, path.stat().st_size),
                RequestContext("terminology-benchmark-contract", run_id=f"parse-{format_id.value}"),
                format_id,
            )
        )
        assert result.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}
        assert result.source_snapshot is not None
        assert result.adapter_id == template["adapter_id"]
        assert result.adapter_version == template["adapter_version"]
