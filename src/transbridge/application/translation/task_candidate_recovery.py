"""Read task-owned translation candidates using durable before-input evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re

from transbridge.application.io.identity import EntryKey, EntryRevision

from .candidate_checkpoint import FilesystemTranslationCheckpointPort
from .candidate_checkpoint_journal import read_object
from .models import TranslationAction
from .workload_models import CandidateTranslation, TranslationInput

logger = logging.getLogger(__name__)
_PHASE_ORDER = {"name-reuse": 0, "names": 1, "": 2}


@dataclass(frozen=True, slots=True)
class RecoveryCheckpointRef:
    run_id: str
    execution: int
    attempt: int
    phase: str
    path: Path

    @property
    def priority(self):
        return self.execution, self.attempt, _PHASE_ORDER[self.phase]


def source_digest(source_key: str) -> str:
    return hashlib.sha256(source_key.encode()).hexdigest()[:20]


def checkpoint_references(root: Path, execution_runs, source_key: str, *, strict=True, identities=None):
    """Associate files by exact task/source identities, never by display labels."""
    roots = tuple(dict.fromkeys(str(value) for value in execution_runs))
    expressions = tuple(
        re.compile(
            rf"{re.escape(run_id)}(?:-attempt-(?P<attempt>[1-9][0-9]*))?"
            rf"-{source_digest(source_key)}(?P<phase>-names|-name-reuse)?"
        )
        for run_id in roots
    )
    records = checkpoint_identities(root, strict=strict) if identities is None else identities
    for path, run_id in records:
        for execution, expression in reversed(tuple(enumerate(expressions))):
            match = expression.fullmatch(run_id)
            if match is None:
                continue
            expected = f"translation-{hashlib.sha256(run_id.encode()).hexdigest()}.json"
            if path.name != expected:
                raise ValueError("恢复检查点的任务身份与文件名不一致，原文件已保留。")
            yield RecoveryCheckpointRef(
                run_id, execution, int(match["attempt"] or 0), (match["phase"] or "").removeprefix("-"), path
            )
            break


def checkpoint_identities(root, *, strict):
    """Inspect only checkpoint heads, keeping full journal replay out of discovery."""
    for path in sorted(Path(root).glob("translation-*.json")):
        try:
            payload = read_object(path)
            identity = payload.get("identity", payload)
            run_id = identity.get("run_id") if isinstance(identity, dict) else None
            if not isinstance(run_id, str) or not run_id.strip():
                raise ValueError("translation checkpoint identity is missing")
        except ValueError as exc:
            if strict:
                raise ValueError(f"检查点身份损坏（{path.name}），无法安全确认旧任务进度，原文件已保留。") from exc
            logger.warning("Cannot identify translation checkpoint %s; original retained", path, exc_info=True)
            continue
        yield path, run_id


def recovery_inputs(source: Mapping) -> dict[EntryKey, TranslationInput]:
    """Parse the immutable original task scope used to validate every candidate."""
    rows = source.get("inputs", ())
    if not isinstance(rows, (tuple, list)):
        raise ValueError("恢复任务的原始输入快照无效。")
    result = {}
    for row in rows:
        try:
            entry = TranslationInput(
                EntryKey.from_dict(row["entry_key"]),
                EntryRevision(row["revision"]),
                row["original"],
                row["translation"],
                row["stage"],
                row.get("context", ""),
                row.get("terminology_plugin_id"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("恢复任务的原始输入快照无效。") from exc
        if entry.entry_key in result:
            raise ValueError("恢复任务的原始输入包含重复词条。")
        if entry.entry_key.original is not None and entry.entry_key.original != entry.original:
            raise ValueError("恢复任务的词条身份与原文不一致。")
        result[entry.entry_key] = entry
    return result


def _checkpoint_stems(source):
    stems = source.get("checkpoint_stems")
    if stems is None:
        stem = source.get("checkpoint_stem") or source.get("esp_stem")
        if not stem:
            path = source.get("esp_path")
            stem = Path(path).stem if path else f"project-source-{source_digest(source['key'])}"
        stems = (stem,)
    if not isinstance(stems, (tuple, list)) or any(
        not isinstance(stem, str)
        or not stem.strip()
        or stem in {".", ".."}
        or any(char in stem for char in ("/", "\\", ":"))
        for stem in stems
    ):
        raise ValueError("恢复任务的检查点目录身份无效。")
    return tuple(dict.fromkeys(stems))


def _execution_inputs(record, execution_id, source_key, original, candidate_keys):
    snapshots = record.get("execution_inputs", {})
    if not isinstance(snapshots, dict):
        raise ValueError("恢复任务的执行输入快照无效。")
    if execution_id not in snapshots:
        return original
    sources = snapshots[execution_id]
    if not isinstance(sources, (list, tuple)):
        raise ValueError("恢复任务的执行来源快照无效。")
    matching = [source for source in sources if isinstance(source, Mapping) and source.get("key") == source_key]
    if len(matching) != 1:
        raise ValueError("恢复任务缺少唯一的执行来源快照。")
    inputs = recovery_inputs(matching[0])
    # Initialization captures the selected source before completed results are
    # restored. Some of those rows may already contain their accepted after-text
    # and never execute in this run. Only candidate-producing inputs authorize
    # reuse; unrelated restored rows must not invalidate the remaining work.
    for key in candidate_keys:
        entry = inputs.get(key)
        before = original.get(key)
        if (
            entry is None
            or before is None
            or (entry.original, entry.translation, entry.stage, entry.context)
            != (
                before.original,
                before.translation,
                before.stage,
                before.context,
            )
        ):
            raise ValueError("恢复任务执行输入的内容与原始范围不一致。")
    return inputs


def load_recovery_candidates(record, source_key: str, config) -> dict[EntryKey, CandidateTranslation]:
    """Return validated draft seeds; callers still verify the current project input.

    Commit markers refer to private drafts, so they never make a candidate a
    saved project result. Older phases and attempts provide missing entries only;
    a later candidate for the same key wins deterministically.
    """
    sources = [source for source in record["sources"] if source["key"] == source_key]
    if len(sources) != 1:
        raise ValueError("恢复任务的来源身份不存在或重复。")
    source = sources[0]
    inputs = recovery_inputs(source)
    scope = {EntryKey.from_dict(key) for key in source["translate_keys"]}
    if not scope <= inputs.keys():
        raise ValueError("恢复翻译范围缺少原始输入快照。")
    runs = record.get("execution_runs") or (record["task_id"],)
    if not isinstance(runs, (list, tuple)) or any(not isinstance(run, str) or not run.strip() for run in runs):
        raise ValueError("恢复任务执行链无效。")
    runs = tuple(dict.fromkeys(runs))
    references = []
    for stem in _checkpoint_stems(source):
        root = Path(config.get_ai_translator_dir(stem)) / "runtime-v2"
        references.extend(checkpoint_references(root, runs, source_key))
    recovered = {}
    seen_runs = set()
    for reference in sorted(references, key=lambda value: value.priority):
        if reference.run_id in seen_runs:
            raise ValueError("同一恢复任务检查点出现在多个来源目录，无法确定有效副本。")
        seen_runs.add(reference.run_id)
        try:
            checkpoint = FilesystemTranslationCheckpointPort(reference.path.parent).load_readonly(reference.run_id)
        except (OSError, ValueError) as exc:
            raise ValueError(f"读取恢复检查点失败（{reference.path.name}），原文件已保留：{exc}") from exc
        if checkpoint is None:
            raise ValueError("恢复检查点在读取期间被移除，请刷新任务记录。")
        execution_inputs = _execution_inputs(
            record,
            runs[reference.execution],
            source_key,
            inputs,
            {candidate.entry_key for candidate in checkpoint.candidates},
        )
        for candidate in checkpoint.candidates:
            before = execution_inputs.get(candidate.entry_key)
            if candidate.entry_key not in scope or before is None:
                raise ValueError("恢复检查点包含原任务翻译范围之外的词条。")
            if candidate.action is not TranslationAction.TRANSLATE or candidate.before_revision != before.revision:
                raise ValueError("恢复候选与原任务输入版本或翻译动作不一致。")
            recovered[candidate.entry_key] = candidate
    return recovered
