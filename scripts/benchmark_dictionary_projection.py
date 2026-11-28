# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Measure dictionary writes after saving a synthetic paged project in default A and profile B."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import platform
from shutil import copy2
import sqlite3
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.application.io.contracts import FormatId
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects.dictionary_apply import dictionary_library
from transbridge.application.projects.storage_contracts import ExtractedSource, StoredEntry
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import (
    BuildResult,
    BuildResultRef,
    BuildSummary,
    CanonicalDiff,
    DecisionStatus,
    TermDecision,
    TerminologyVersion,
    TerminologyVersionRef,
)
from transbridge.application.terminology.publish import terminology_version_content_digest
from transbridge.application.terminology_profiles import (
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.config.text_encoding import TextEncodings
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.entry_codec import encode_json
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.writes import insert_project
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import VariantEntryState
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.shell.dictionary_save_changes import DictionarySaveChanges
from transbridge.ui.shell.saved_dictionary import SavedDictionarySource

PROJECT = ProjectId("dictionary-projection-benchmark")
VARIANT = VariantId("main")
CONTEXT = RequestContext("benchmark", run_id="dictionary-projection")
NAMESPACE = SourceNamespace("benchmark:whiterun")
MOD = "Synthetic.esp"
SPELLING = {"default": "雪漫", "profile": "白漫"}
SCENARIOS = ("first", "unchanged", "one_edit")


def entry_key(index):
    return EntryKey(NAMESPACE, f"{index + 1:08X}|0~NPC_:FULL")


def translation(spelling, index):
    return f"{spelling}居民 {index:06d}"


def create_project(path, count, spelling):
    def rows():
        for index in range(count):
            key = entry_key(index)
            text = translation(spelling, index)
            snapshot = EntrySnapshot(
                key,
                key.local_key,
                f"Whiterun Resident {index:06d}",
                text,
                Stage.REVIEWED.value,
                "NPC_:FULL",
                (),
                EntryRevision(),
                (),
                (("plugin.form_id", f"{index + 1:08X}|{MOD}"),),
            )
            yield StoredEntry(snapshot, VariantEntryState(key, text, Stage.REVIEWED))

    source = ExtractedSource(NAMESPACE, MOD, FormatId.PLUGIN_SSE, "1" * 64, tuple(rows()))
    with project_connection(path, write=True, create=True) as connection:
        insert_project(connection, PROJECT, "Dictionary projection benchmark", VARIANT, "Main", (source,))


def profile_evidence():
    content = TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫"),))
    profile = PublishedTerminologyProfile(
        "profile-b", PROJECT.value, 1, "B", content.content_digest, content, "2026-01-01T00:00:00Z"
    )
    base = EffectiveTerminologySnapshot(
        PROJECT.value,
        VARIANT.value,
        EffectiveSnapshotStatus.READY,
        "default-a",
        "2" * 64,
        decisions=(
            TermDecision(
                "term-whiterun",
                PROJECT.value,
                VARIANT.value,
                "Whiterun",
                "whiterun",
                "雪漫",
                status=DecisionStatus.ADOPTED,
            ),
        ),
    )
    return profile, base


def seed_sqlite_terminology(terminology, project):
    profile, base = profile_evidence()
    profiles = terminology.profile_service_for(PROJECT.value)
    draft = profiles.create_with_content(PROJECT.value, profile.name, profile.content)
    published = profiles.publish(draft.profile_id, expected_draft_revision=draft.draft_revision)
    repository = terminology.repositories.for_project(PROJECT.value)
    build = BuildResult(
        BuildResultRef("benchmark", "benchmark"), PROJECT.value, VARIANT.value, BuildSummary(1, 0, 0, 0)
    )
    repository.put_build(build)
    version = TerminologyVersion(
        TerminologyVersionRef(base.version_id, PROJECT.value, VARIANT.value, base.content_digest),
        None,
        build.ref,
        0,
        0,
        build.completeness,
        published.published_at,
        base.decisions,
        CanonicalDiff(None, base.version_id, "benchmark", ()),
    )
    version = replace(version, ref=replace(version.ref, content_digest=terminology_version_content_digest(version)))
    repository.publish_version(version, expected_effective_version_id=None)
    selection = TerminologyProfileSelection(
        PROJECT.value, VARIANT.value, published.profile_id, published.revision, published.published_at
    )
    with project_connection(project, write=True) as connection:
        connection.execute(
            "UPDATE variant SET terminology_selection = ? WHERE identity = ?",
            (encode_json(asdict(selection)), VARIANT.value),
        )


@dataclass
class SaveMeter:
    collection: ProjectRowsCollection
    projection_requests: int = 0
    rows: int = 0
    reads: int = 0
    full_reads: int = 0

    def read(self, project_revision, variant_revision, keys):
        self.reads += 1
        self.full_reads += keys is None
        for row in self.collection.saved_entries(project_revision, variant_revision, keys):
            self.rows += 1
            yield row

    def measure(self, source, receipt, batches):
        self.rows = self.reads = self.full_reads = self.projection_requests = 0
        start = perf_counter()
        changed = source.publish(receipt, batches)
        return {
            "elapsed_ms": (perf_counter() - start) * 1000,
            "changed": changed,
            "rows": self.rows,
            "reads": self.reads,
            "full_reads": self.full_reads,
            "projection_requests": self.projection_requests,
        }


def require(result):
    if not result.is_success:
        raise RuntimeError(", ".join(item.message for item in result.diagnostics))
    return result.value


def measure_project(workspace, project, mode, count, *, sqlite_terminology):
    with ExitStack() as stack:
        if sqlite_terminology:
            runtime = build_runtime({"persistence_v2_root": workspace, "ui_config_path": workspace / "config.toml"})
            stack.callback(lambda: require(runtime.close()))
            services = runtime.use_cases.resolve("persistence_v2")
            terminology = runtime.use_cases.resolve("terminology_profile_service_factory")
            if mode == "profile":
                seed_sqlite_terminology(terminology, project)
        else:
            services = build_persistence_v2_services(
                workspace, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "benchmark"
            )
            stack.callback(services.close)
        opener = services.current_project_opener
        prepared = require(opener.prepare_path(project, CONTEXT, paged=True))
        require(opener.activate(prepared, CONTEXT))
        reader = prepared.records.paged_read
        (source,) = reader.sources
        meter = SaveMeter(ProjectRowsCollection(reader, source))
        library_path = workspace / "dictionary"
        identity = (PROJECT.value, VARIANT.value)
        if sqlite_terminology:

            def measure_lookup(project_id, variant_id):
                meter.projection_requests += 1
                return terminology.dictionary_terminology(project_id, variant_id)

            binding = SimpleNamespace(
                terminology_profile_service_factory=SimpleNamespace(dictionary_terminology=measure_lookup),
                active_version_identity=identity,
            )
            library = dictionary_library(binding, library_path, encodings=TextEncodings())
        elif mode == "profile":
            from transbridge.application.terminology_profiles.dictionary_projection import DictionaryTextProjection

            profile, base = profile_evidence()

            def projection_factory(*, to_default):
                if not to_default:
                    raise RuntimeError("a dictionary save requested the apply projection")
                meter.projection_requests += 1
                return DictionaryTextProjection(profile, base, to_default=to_default)

            library = DictionaryLibrary(library_path, projection_factory=projection_factory)
        else:
            library = DictionaryLibrary(library_path)

        publisher = SavedDictionarySource(
            identity,
            MOD,
            library,
            meter.read,
            source.info.namespace.value,
            source.info.fingerprint,
        )
        changes = DictionarySaveChanges()
        commands = services.gui_project_commands
        measurements = {}
        index = count // 2
        key = entry_key(index)
        edited_text = translation(SPELLING[mode], index) + "（修订）"
        for scenario in SCENARIOS:
            if scenario == "one_edit":
                require(commands.update_entry(key, CONTEXT, translation=edited_text))
            receipt = require(commands.save(CONTEXT, include_entry_delta=True))
            measurements[scenario] = meter.measure(publisher, receipt, changes.record(receipt))
        expected = [(count, count, 1, 1), (0, 0, 0, 0), (1, 1, 1, 0)]
        actual = [(item["changed"], item["rows"], item["reads"], item["full_reads"]) for item in measurements.values()]
        if actual != expected:
            raise RuntimeError(f"{mode} write counts differ: {actual} != {expected}")
        if mode == "profile" or sqlite_terminology:
            first, unchanged, edited = (measurements[scenario]["projection_requests"] for scenario in SCENARIOS)
            if first != 1 or unchanged > 1 or edited != 1:
                raise RuntimeError("dictionary publication did not resolve projection at most once per save")
        (edited,) = reader.store.read_entries(VARIANT, (key,))
        if edited.state.translation != edited_text:
            raise RuntimeError(f"{mode} project did not retain the user-edited spelling")
        return measurements, library_path / "libraries.sqlite3"


def verify_libraries(paths, count):
    with ExitStack() as stack:
        databases = [stack.enter_context(closing(sqlite3.connect(path))) for path in paths]
        records = [db.execute("SELECT * FROM library_records ORDER BY original") for db in databases]
        verified = 0
        for rows in zip(*records, strict=True):
            first = rows[0]
            index = int(first[6].removeprefix("Whiterun Resident "))
            expected = translation(SPELLING["default"], index)
            if index == count // 2:
                expected += "（修订）"
            if first[7] != expected or any(row != first for row in rows[1:]):
                raise RuntimeError(f"dictionary rows differ from default A at {first[6]}")
            verified += 1
        if verified != count:
            raise RuntimeError(f"expected {count} dictionary records, found {verified}")


def summarize(pairs, modes):
    result = {}
    for mode in modes:
        result[mode] = {}
        for scenario in SCENARIOS:
            samples = [pair[mode][scenario] for pair in pairs]
            timing = [sample["elapsed_ms"] for sample in samples]
            result[mode][scenario] = {
                "median_ms": median(timing),
                "min_ms": min(timing),
                "max_ms": max(timing),
                "rows": samples[0]["rows"],
                "changed": samples[0]["changed"],
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--mode", choices=("both", *SPELLING), default="both")
    parser.add_argument("--sqlite-terminology", action="store_true", help="include production terminology reads")
    options = parser.parse_args()
    if not 1 <= options.rows <= 999_999 or options.repetitions < 1:
        parser.error("use 1–999,999 rows and at least one repetition")
    modes = tuple(SPELLING) if options.mode == "both" else (options.mode,)
    pairs = []
    with TemporaryDirectory(prefix=".benchmark-dictionary-", dir=Path.cwd()) as temporary:
        scratch = Path(temporary)
        seeds = {mode: scratch / f"{mode}.sqlite3" for mode in modes}
        for mode, seed in seeds.items():
            create_project(seed, options.rows, SPELLING[mode])
        project_bytes = {mode: seed.stat().st_size for mode, seed in seeds.items()}
        for repetition in range(options.repetitions):
            with TemporaryDirectory(prefix="pair-", dir=scratch) as directory:
                pair, libraries = {}, []
                order = modes if repetition % 2 == 0 else modes[::-1]
                for mode in order:
                    workspace = Path(directory) / mode
                    project = project_path(workspace, PROJECT)
                    project.parent.mkdir(parents=True)
                    copy2(seeds[mode], project)
                    pair[mode], library = measure_project(
                        workspace, project, mode, options.rows, sqlite_terminology=options.sqlite_terminology
                    )
                    libraries.append(library)
                verify_libraries(libraries, options.rows)
                pairs.append(pair)
    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "sqlite_terminology": options.sqlite_terminology,
                "rows": options.rows,
                "project_bytes": project_bytes,
                "repetitions": options.repetitions,
                "summary": summarize(pairs, modes),
                "pairs": pairs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
