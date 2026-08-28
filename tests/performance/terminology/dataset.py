"""Deterministic project-terminology benchmark dataset generation.

The large benchmark corpora are generated at runtime.  Only the small, real
adapter fixtures already maintained by the I/O contract suite are copied into
the generated corpus, which keeps the repository small while pinning the exact
bytes used to prove adapter readability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
import shutil
from typing import Any

from transbridge.application.io import (
    EetXmlAdapter,
    FormatId,
    LocalizedStringsAdapter,
    ParatranzJsonAdapter,
    SsePluginAdapter,
    XtXmlAdapter,
)

DATASET_SCHEMA_VERSION = 1
GENERATOR_VERSION = "1.0"
DEFAULT_SEED = 516_000
REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_ADAPTER_FORMAT_COUNT = 5


@dataclass(frozen=True, slots=True)
class TerminologyDatasetSpec:
    """Fixed logical scale and seed for a generated benchmark dataset."""

    name: str
    seed: int
    source_count: int
    evidence_count: int
    terminology_count: int
    conflict_group_count: int
    version_count: int
    schema_version: int = DATASET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DATASET_SCHEMA_VERSION:
            raise ValueError(f"unsupported dataset schema version: {self.schema_version}")
        if not self.name.strip():
            raise ValueError("dataset name must not be empty")
        if self.seed < 0:
            raise ValueError("dataset seed must not be negative")
        values = (
            self.source_count,
            self.evidence_count,
            self.terminology_count,
            self.version_count,
        )
        if min(values) < 1 or self.conflict_group_count < 0:
            raise ValueError("dataset counts must be positive and conflict count must not be negative")
        if self.source_count < REQUIRED_ADAPTER_FORMAT_COUNT:
            raise ValueError(f"source_count must cover all {REQUIRED_ADAPTER_FORMAT_COUNT} real adapter formats")
        if self.conflict_group_count > self.terminology_count:
            raise ValueError("conflict groups cannot exceed terminology count")
        minimum_evidence = self.terminology_count + self.conflict_group_count
        if self.evidence_count < minimum_evidence:
            raise ValueError(f"evidence_count must be at least {minimum_evidence} for the requested conflicts")

    @property
    def candidate_count(self) -> int:
        return self.terminology_count + self.conflict_group_count


SMOKE_DATASET = TerminologyDatasetSpec(
    name="smoke",
    seed=DEFAULT_SEED,
    source_count=5,
    evidence_count=64,
    terminology_count=16,
    conflict_group_count=4,
    version_count=3,
)
REGULAR_DATASET = TerminologyDatasetSpec(
    name="regular",
    seed=DEFAULT_SEED,
    source_count=50,
    evidence_count=250_000,
    terminology_count=50_000,
    conflict_group_count=5_000,
    version_count=10,
)
STRESS_DATASET = TerminologyDatasetSpec(
    name="stress",
    seed=DEFAULT_SEED,
    source_count=200,
    evidence_count=1_000_000,
    terminology_count=200_000,
    conflict_group_count=20_000,
    version_count=50,
)
DATASET_SPECS = {spec.name: spec for spec in (SMOKE_DATASET, REGULAR_DATASET, STRESS_DATASET)}


@dataclass(frozen=True, slots=True)
class _SourceTemplate:
    key: str
    relative_fixture: str
    generated_name: str
    format_id: FormatId
    adapter_id: str
    adapter_version: str


def _source_templates() -> tuple[_SourceTemplate, ...]:
    adapters = (
        (
            "plugin",
            "tests/parser/data/sample.esp",
            "sample.esp",
            FormatId.PLUGIN_SSE,
            SsePluginAdapter(),
        ),
        (
            "eet",
            "tests/contracts/io/fixtures/eet-small.xml",
            "eet-small.xml",
            FormatId.XML_EET,
            EetXmlAdapter(),
        ),
        (
            "xt",
            "tests/contracts/io/fixtures/xt-small.xml",
            "xt-small.xml",
            FormatId.XML_XT,
            XtXmlAdapter(),
        ),
        (
            "strings",
            "tests/contracts/io/fixtures/strings/integrity.strings",
            "integrity.strings",
            FormatId.STRINGS,
            LocalizedStringsAdapter(FormatId.STRINGS),
        ),
        (
            "paratranz",
            "tests/contracts/io/fixtures/paratranz_dual_id.json",
            "paratranz-dual-id.json",
            FormatId.JSON_PARATRANZ,
            ParatranzJsonAdapter(),
        ),
    )
    return tuple(
        _SourceTemplate(
            key=key,
            relative_fixture=relative_fixture,
            generated_name=generated_name,
            format_id=format_id,
            adapter_id=adapter.adapter_id,
            adapter_version=adapter.adapter_version,
        )
        for key, relative_fixture, generated_name, format_id, adapter in adapters
    )


_SOURCE_TEMPLATES = _source_templates()
if len(_SOURCE_TEMPLATES) != REQUIRED_ADAPTER_FORMAT_COUNT:  # pragma: no cover - module contract guard
    raise RuntimeError("benchmark adapter template count drift requires a dataset schema review")


@dataclass(frozen=True, slots=True)
class GeneratedTerminologyDataset:
    """Paths and immutable metadata for one generated dataset instance."""

    root: Path
    spec: TerminologyDatasetSpec
    source_files: tuple[Path, ...]
    source_index_file: Path
    evidence_file: Path
    version_history_file: Path


def generate_terminology_dataset(
    target: Path,
    spec: TerminologyDatasetSpec = SMOKE_DATASET,
) -> GeneratedTerminologyDataset:
    """Generate a self-contained deterministic corpus below ``target``."""

    target = Path(target)
    sources_dir = target / "adapter-sources"
    target.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)

    source_files: list[Path] = []
    template_records: list[dict[str, Any]] = []
    for template in _SOURCE_TEMPLATES:
        source = REPO_ROOT / template.relative_fixture
        if not source.is_file():
            raise FileNotFoundError(f"benchmark adapter fixture is missing: {source}")
        destination = sources_dir / template.generated_name
        shutil.copyfile(source, destination)
        source_files.append(destination)
        template_records.append({
            "template_key": template.key,
            "path": destination.relative_to(target).as_posix(),
            "format_id": template.format_id.value,
            "adapter_id": template.adapter_id,
            "adapter_version": template.adapter_version,
            "size_bytes": destination.stat().st_size,
            "sha256": _file_sha256(destination),
        })

    registrations = []
    for index in range(spec.source_count):
        template = template_records[index % len(template_records)]
        registrations.append({
            "source_id": f"source-{index:04d}",
            "template_key": template["template_key"],
            "format_id": template["format_id"],
            "fingerprint": template["sha256"],
        })
    source_index_file = target / "source-index.json"
    _write_json(
        source_index_file,
        {
            "schema_version": DATASET_SCHEMA_VERSION,
            "templates": template_records,
            "registrations": registrations,
        },
    )

    evidence_file = target / "evidence.ndjson"
    _write_evidence(evidence_file, spec, registrations)
    version_history_file = target / "version-history.json"
    _write_version_history(version_history_file, spec)

    return GeneratedTerminologyDataset(
        root=target,
        spec=spec,
        source_files=tuple(source_files),
        source_index_file=source_index_file,
        evidence_file=evidence_file,
        version_history_file=version_history_file,
    )


def dataset_manifest(dataset: GeneratedTerminologyDataset) -> dict[str, Any]:
    """Return the stable, machine-independent manifest for ``dataset``."""

    payload = _dataset_digest_payload(dataset)
    return {
        **payload,
        "canonical_digest": _canonical_json_sha256(payload),
    }


def canonical_dataset_digest(dataset: GeneratedTerminologyDataset) -> str:
    """Return a digest that changes for any logical corpus or fixture drift."""

    return _canonical_json_sha256(_dataset_digest_payload(dataset))


def _dataset_digest_payload(dataset: GeneratedTerminologyDataset) -> dict[str, Any]:
    source_index = json.loads(dataset.source_index_file.read_text(encoding="utf-8"))
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "spec": asdict(dataset.spec),
        "expected_counts": {
            "sources": dataset.spec.source_count,
            "evidence": dataset.spec.evidence_count,
            "candidates": dataset.spec.candidate_count,
            "terminology": dataset.spec.terminology_count,
            "conflict_groups": dataset.spec.conflict_group_count,
            "versions": dataset.spec.version_count,
        },
        "adapter_templates": source_index["templates"],
        "artifacts": {
            "source_index": {
                "path": dataset.source_index_file.relative_to(dataset.root).as_posix(),
                "size_bytes": dataset.source_index_file.stat().st_size,
                "sha256": _file_sha256(dataset.source_index_file),
            },
            "evidence": {
                "path": dataset.evidence_file.relative_to(dataset.root).as_posix(),
                "size_bytes": dataset.evidence_file.stat().st_size,
                "sha256": _file_sha256(dataset.evidence_file),
            },
            "version_history": {
                "path": dataset.version_history_file.relative_to(dataset.root).as_posix(),
                "size_bytes": dataset.version_history_file.stat().st_size,
                "sha256": _file_sha256(dataset.version_history_file),
            },
        },
    }


def _write_evidence(path: Path, spec: TerminologyDatasetSpec, registrations: list[dict[str, Any]]) -> None:
    rng = random.Random(spec.seed)
    required: list[tuple[int, int]] = [(term_index, 0) for term_index in range(spec.terminology_count)]
    required.extend((term_index, 1) for term_index in range(spec.conflict_group_count))

    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for evidence_index in range(spec.evidence_count):
            if evidence_index < len(required):
                term_index, translation_index = required[evidence_index]
            else:
                term_index = rng.randrange(spec.terminology_count)
                translation_index = rng.randrange(2) if term_index < spec.conflict_group_count else 0
            source = registrations[rng.randrange(len(registrations))]
            record = {
                "evidence_id": f"evidence-{evidence_index:09d}",
                "source_id": source["source_id"],
                "format_id": source["format_id"],
                "locator": f"record/{evidence_index:09d}",
                "original": f"Source term {term_index:06d}",
                "translation": f"译名 {term_index:06d}-{translation_index}",
            }
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
            stream.write("\n")


def _write_version_history(path: Path, spec: TerminologyDatasetSpec) -> None:
    versions = [
        {
            "version_id": f"version-{index + 1:03d}",
            "parent_version_id": None if index == 0 else f"version-{index:03d}",
            "sequence": index + 1,
            "terminology_count": spec.terminology_count,
        }
        for index in range(spec.version_count)
    ]
    _write_json(path, {"schema_version": DATASET_SCHEMA_VERSION, "versions": versions})


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
