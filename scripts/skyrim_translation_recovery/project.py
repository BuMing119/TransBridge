"""Build recoverable project baselines and publish through the production lifecycle."""

from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from transbridge.application.io import FormatId
from transbridge.application.io.identity import Provenance
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.parser.strings_file import PluginStringsWriter
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer

from .inputs import SUFFIXES, capture_plugin, read_table
from .matching import Decision, field, recover


def locate(entry, tables):
    candidates = [suffix for suffix, values in tables.items() if entry.string_id in values]
    if len(candidates) == 1:
        return candidates[0], entry.string_id
    suffix = PluginStringsWriter._ext_for(field(entry.entry_key.local_key).rsplit(":", 1)[-1])
    if suffix in candidates and tables[suffix][entry.string_id] == entry.original:
        return suffix, entry.string_id
    return None


def reconcile_shared_ids(entries, locations, decisions):
    groups = defaultdict(list)
    for i, location in enumerate(locations):
        if location is not None:
            groups[location].append(i)
    for indices in groups.values():
        if len(indices) < 2:
            continue
        choices = [decisions[i] for i in indices]
        originals = {entries[i].original for i in indices}
        para = [d for d in choices if d.reason.startswith("para") and (d.translation or d.stage != 0)]
        preferred = para or [d for d in choices if d.translation] or choices
        states = {(d.translation, d.stage) for d in preferred}
        if len(states) > 1 or len(originals) > 1 or any(d.reason == "para_conflict" for d in choices):
            selected = Decision("", 0, "shared_id_conflict", tuple(sorted({r for d in choices for r in d.references})))
        else:
            selected = preferred[0]
        for i in indices:
            decisions[i] = selected


class RecoverySources:
    def __init__(self, output, ank_dir, language, index, run_id):
        self.output, self.ank_dir, self.language, self.index, self.run_id = output, ank_dir, language, index, run_id
        self.prepared = {}
        self.used = set()
        self.stats = Counter()
        self.sources = []
        self.ank_files = {p.name.casefold(): p for p in ank_dir.iterdir() if p.is_file()}
        self.timestamp = datetime.now(UTC).isoformat()

    def prepare(self, plugin: Path, trace, context):
        target, fingerprints = capture_plugin(plugin, self.output / "sources" / plugin.name, self.language)
        original, ank, compatibility = {}, {}, []
        for suffix in SUFFIXES:
            name = f"{plugin.stem}_{self.language}{suffix}"
            source_path = next(p for p in (target.parent / "Strings").iterdir() if p.name.casefold() == name.casefold())
            original[suffix], _ = read_table(source_path)
            ank_path = self.ank_files.get(name.casefold())
            ank[suffix], sha = read_table(ank_path) if ank_path is not None else ({}, None)
            compatibility.append({
                "file": str(ank_path) if ank_path else name,
                "sha256": sha,
                "missing_ids": sorted(original[suffix].keys() - ank[suffix].keys()),
                "extra_ids": sorted(ank[suffix].keys() - original[suffix].keys()),
            })
        prepared = TranslationIoProjectSourcePreparer().prepare_source(
            ProjectSourceRequest(str(target), FormatId.PLUGIN_SSE),
            context,
            role="primary",
            common_options=(("language", self.language), ("skip_empty", False)),
        )
        if prepared.hydration is None:
            raise ValueError(f"Missing source hydration: {plugin}")
        entries = prepared.hydration.entries
        locations = [locate(entry, original) for entry in entries]
        decisions = [
            recover(
                self.index,
                plugin.stem.casefold(),
                entry.entry_key.local_key,
                entry.original,
                ank[location[0]].get(location[1]) if location else None,
            )
            for entry, location in zip(entries, locations, strict=True)
        ]
        before_reconcile = list(decisions)
        reconcile_shared_ids(entries, locations, decisions)
        states = []
        counts = Counter()
        for baseline, entry, location, decision, initial in zip(
            prepared.baseline.entries, entries, locations, decisions, before_reconcile, strict=True
        ):
            if baseline.entry_key != entry.entry_key:
                raise ValueError("Baseline and hydration entry order disagree")
            counts[decision.reason] += 1
            self.used.update(decision.references)
            provenance = Provenance(
                self.run_id,
                "skyrim-recovery",
                decision.reason,
                self.timestamp,
                (("plugin", plugin.name), ("references_json", json.dumps(decision.references, ensure_ascii=False))),
            )
            states.append(
                replace(
                    baseline, translation=decision.translation, stage=Stage(decision.stage), provenance=(provenance,)
                )
            )
            trace.write(
                json.dumps(
                    {
                        "plugin": plugin.name,
                        "entry_key": entry.entry_key.to_dict(),
                        "original": entry.original,
                        "string_location": location,
                        "translation": decision.translation,
                        "stage": decision.stage,
                        "reason": decision.reason,
                        "para_references": decision.references,
                        "initial_choice": {
                            "text": initial.translation,
                            "stage": initial.stage,
                            "reason": initial.reason,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        referenced = set(locations)
        unsupported = [
            {"suffix": suffix, "id": sid, "original": text, "ank": ank[suffix][sid]}
            for suffix, table in original.items()
            for sid, text in table.items()
            if (suffix, sid) not in referenced and ank[suffix].get(sid) and ank[suffix][sid] != text
        ]
        self.prepared[str(target)] = replace(prepared, baseline=replace(prepared.baseline, entries=tuple(states)))
        self.stats.update(counts)
        self.sources.append({
            "plugin": plugin.name,
            "fingerprints": fingerprints,
            "ank_compatibility": compatibility,
            "counts": dict(counts),
            "parser_diagnostics": [
                {"code": d.code, "message": d.message, "details": dict(d.details)} for d in prepared.diagnostics
            ],
            "unrepresented_ank_candidates": unsupported,
        })
        print(f"{plugin.name}: {len(entries)} entries; {dict(counts)}", flush=True)
        return target

    def prepare_source(self, request, context, *, role, common_options):
        if role != "primary":
            raise ValueError("Recovery only provisions primary sources")
        return self.prepared[request.location]


def require_success(result):
    if not result.is_success:
        raise ValueError("; ".join(f"{d.code}: {d.message}" for d in result.diagnostics))
    return result.value


def build_services(root, **kwargs):
    return build_persistence_v2_services(
        root,
        id_factory=lambda: uuid4().hex,
        timestamp_factory=lambda: datetime.now(UTC).isoformat(),
        **kwargs,
    )


def publish(output: Path, recovery: RecoverySources, name: str, context, install_root: Path | None):
    archive = output / "recovered.transbridge"
    with TemporaryDirectory(prefix=".build-", dir=output) as directory:
        with closing(build_services(directory, source_preparer=recovery)) as services:
            requests = tuple(ProjectSourceRequest(path, FormatId.PLUGIN_SSE) for path in recovery.prepared)
            created = require_success(
                services.gui_project_commands.create_project(
                    ProjectProvisioningRequest(name, "恢复译文", source=requests[0], additional_sources=requests[1:]),
                    context,
                )
            )
            active = services.project_lifecycle.active
            expected = {
                e.entry_key: (e.translation, e.stage) for p in recovery.prepared.values() for e in p.baseline.entries
            }
            actual = {e.entry_key: (e.translation, e.stage) for e in active.variant.snapshot().entries}
            if actual != expected:
                raise ValueError("Provisioned project differs from recovery decisions")
            services.project_archive.export_project(str(archive), context)
    project_path = None
    if install_root is not None:
        with closing(build_services(install_root)) as services:
            project_path = services.project_archive.import_project(str(archive), context)
    return {"archive": str(archive), "created": created, "installed_project_path": project_path}
