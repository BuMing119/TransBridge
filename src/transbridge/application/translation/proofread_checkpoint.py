"""Atomic, project-scoped proofreading candidates, independent of task reports."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
import uuid

from transbridge.converter.translation_entry import STAGE_QUESTIONABLE

from .postprocess import PostProcessCandidate
from .proofread_advisory import has_review_evidence, prepare_review_candidate
from .protected_syntax import protected_syntax_matches

logger = logging.getLogger(__name__)


class _IncompatibleCheckpointSchema(ValueError):
    """A readable database whose layout cannot safely store current candidates."""


def _check_schema(db: sqlite3.Connection, *, allow_empty: bool = False) -> None:
    objects = db.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if allow_empty and not objects:
        return
    columns = [tuple(row[1:]) for row in db.execute("PRAGMA table_info(candidates)")]
    if objects != [("table", "candidates")] or columns != [
        ("input", "TEXT", 0, None, 1),
        ("payload", "TEXT", 1, None, 0),
    ]:
        raise _IncompatibleCheckpointSchema("proofread checkpoint schema is not compatible with this version")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def proofread_settings(config: object) -> dict:
    """Only output-affecting settings; never persist credentials or operational limits."""
    names = (
        "provider",
        "base_url",
        "model",
        "temperature",
        "target_lang",
        "game_profile",
        "pp_strategy",
        "pp_polish_level",
        "max_output_tokens",
    )
    return {name: getattr(config, name, None) for name in names}


def _input(candidate: PostProcessCandidate, *, legacy_revision: bool = False) -> str:
    # A reopened collection may reconstruct revision counters. Persist semantic
    # input identity; restoration retains the current candidate's guarded revision.
    values = {
        "key": candidate.entry_key.to_dict(),
        "original": candidate.original,
        "before": candidate.before_text,
        "context": candidate.context,
        "stage": candidate.stage,
        "detected_issues": dict(candidate.report_details).get("detected_issues", ()),
        "issues": dict(candidate.report_details).get("issues", ()),
    }
    if legacy_revision:
        values["revision"] = candidate.before_revision.value
    else:
        values.update(contract="proofread-content-v2", text=candidate.text, plugin=candidate.terminology_plugin_id)
    return _digest(values)


class ProofreadCheckpoint:
    """Commit each completed batch before it is reported as progress.

    Payloads are keyed by original input inside an explicitly selected task scope.
    A separate connection per operation permits use from worker threads; transactions
    ensure an interrupted write exposes either the previous batch or the new one.
    """

    VERSION = 1

    def __init__(
        self,
        root: Path,
        *,
        project: str,
        variant: str,
        source: str,
        settings: Mapping,
        reuse: bool = True,
        task_id: str | None = None,
    ):
        scope = _digest([self.VERSION, project, variant, source, dict(settings)])
        directory = Path(root).resolve()
        if task_id is not None:
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("proofread checkpoint task_id must be a non-empty string")
            directory = directory / "tasks" / _digest(task_id)
        self.path = directory / f"{scope}.sqlite3"
        self.reuse = reuse
        self._write_lock = threading.Lock()
        self._invalid_payloads = False

    def saved_count(self) -> int:
        """Historical well-formed candidates; not a promise of validity for current inputs."""
        if not self.path.exists():
            return 0
        try:
            with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
                _check_schema(db)
                return db.execute(
                    "SELECT COUNT(*) FROM candidates WHERE CASE WHEN json_valid(payload) THEN "
                    "json_type(payload, '$.text') = 'text' AND trim(json_extract(payload, '$.text')) != '' "
                    "AND json_type(payload, '$.terms') = 'text' "
                    "AND length(json_extract(payload, '$.terms')) = 64 "
                    "AND json_extract(payload, '$.terms') NOT GLOB '*[^0-9a-f]*' "
                    "AND json_extract(payload, '$.review.review_contract_version') = 1 "
                    "AND json_extract(payload, '$.review.review_status') IN "
                    "('revised', 'retained', 'term_suspect', 'needs_review') "
                    "AND json_type(payload, '$.review.review_reason') = 'text' "
                    "AND trim(json_extract(payload, '$.review.review_reason')) != '' "
                    "AND json_type(payload, '$.review.detected_issues') = 'array' "
                    "AND json_type(payload, '$.review.review_reference_terms') = 'object' ELSE 0 END"
                ).fetchone()[0]
        except (sqlite3.Error, _IncompatibleCheckpointSchema) as exc:
            logger.warning("Cannot count proofread checkpoint %s; treating it as unavailable: %s", self.path, exc)
            return 0

    def read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
                _check_schema(db)
                rows = {}
                for key, payload in db.execute("SELECT input, payload FROM candidates"):
                    try:
                        row = json.loads(payload)
                        if (
                            not isinstance(row, dict)
                            or not isinstance(row.get("text"), str)
                            or not row["text"].strip()
                            or not isinstance(row.get("terms"), str)
                            or len(row["terms"]) != 64
                            or any(char not in "0123456789abcdef" for char in row["terms"])
                        ):
                            raise ValueError("校对断点内容格式无效")
                    except (TypeError, ValueError) as exc:
                        self._invalid_payloads = True
                        logger.warning("Skipping invalid proofread candidate in %s: %s", self.path, exc)
                        continue
                    rows[key] = row
                return rows
        except (sqlite3.Error, _IncompatibleCheckpointSchema) as exc:
            logger.warning("Cannot read proofread checkpoint %s; recomputing candidates: %s", self.path, exc)
            return {}

    def write(self, rows: list[tuple[str, dict]]) -> None:
        if not rows:
            return
        try:
            with self._write_lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self._invalid_payloads:
                    backup = self.path.with_name(f"corrupt-{uuid.uuid4().hex}.sqlite3")
                    with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(backup)) as destination:
                        source.backup(destination)
                    self._invalid_payloads = False
                    logger.warning("Preserved invalid proofread candidates in %s", backup)
                try:
                    self._write_rows(rows)
                except (sqlite3.DatabaseError, _IncompatibleCheckpointSchema) as exc:
                    if not isinstance(exc, _IncompatibleCheckpointSchema) and getattr(
                        exc, "sqlite_errorcode", 0
                    ) & 0xFF not in {
                        sqlite3.SQLITE_CORRUPT,
                        sqlite3.SQLITE_NOTADB,
                    }:
                        raise
                    backup = self.path.with_name(f"corrupt-{uuid.uuid4().hex}.sqlite3")
                    self.path.rename(backup)
                    logger.warning("Preserved corrupt proofread checkpoint in %s: %s", backup, exc)
                    self._write_rows(rows)
        except (OSError, sqlite3.Error, _IncompatibleCheckpointSchema) as exc:
            raise RuntimeError(f"无法保存校对断点 {self.path}：{exc}") from exc

    def _write_rows(self, rows: list[tuple[str, dict]]) -> None:
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            _check_schema(db, allow_empty=True)
            db.execute("CREATE TABLE IF NOT EXISTS candidates (input TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.executemany(
                "INSERT OR REPLACE INTO candidates VALUES (?, ?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in rows],
            )

    def session(self, candidates: tuple[PostProcessCandidate, ...]) -> ProofreadCheckpointSession:
        return ProofreadCheckpointSession(self, candidates)


class ProofreadCheckpointSession:
    """Bind persisted candidates to current inputs and freshly resolved terminology."""

    def __init__(self, store: ProofreadCheckpoint, candidates: tuple[PostProcessCandidate, ...]):
        self.store = store
        self.inputs = {item.entry_key: item for item in candidates}
        self.terms: dict[object, dict] = {}
        self._lock = threading.Lock()

    def observe(self, candidate, terms):
        with self._lock:
            self.terms[candidate.entry_key] = dict(terms)

    def restore(self, resolver):
        if not self.store.reuse:
            return {}
        rows = self.store.read()
        restored = {}
        for candidate in self.inputs.values():
            row = rows.get(_input(candidate))
            if row is None and candidate.text == candidate.before_text:
                # Read old revision-bound keys without rewriting historical data.
                row = rows.get(_input(candidate, legacy_revision=True))
            if row is None:
                continue
            terms = dict(resolver(candidate))
            text = row.get("text")
            if row.get("terms") != _digest(terms):
                continue
            if not isinstance(text, str) or not text.strip() or not protected_syntax_matches(candidate.original, text):
                continue
            evidence = row.get("review", {})
            if not isinstance(evidence, dict) or not has_review_evidence(evidence):
                continue
            if evidence["review_reference_terms"] != terms:
                continue
            current = dict(prepare_review_candidate(candidate, terms).report_details)
            if evidence.get("detected_issues") != current.get("detected_issues"):
                continue
            restored[candidate.entry_key] = replace(
                candidate,
                text=text,
                phases=("proofread",),
                accepted=True,
                stage=STAGE_QUESTIONABLE if evidence["review_status"] == "needs_review" else candidate.stage,
                report_details=tuple({**dict(candidate.report_details), **evidence}.items()),
            )
            self.observe(candidate, terms)
        return restored

    def save(self, candidates):
        rows = []
        with self._lock:
            for candidate in candidates:
                original = self.inputs[candidate.entry_key]
                terms = self.terms.get(candidate.entry_key)
                if terms is None or not candidate.accepted or "proofread" not in candidate.phases:
                    continue
                if not candidate.text.strip() or not protected_syntax_matches(original.original, candidate.text):
                    continue
                evidence = dict(candidate.report_details)
                if not has_review_evidence(evidence):
                    continue
                rows.append((
                    _input(original),
                    {
                        "text": candidate.text,
                        "terms": _digest(terms),
                        "stage": candidate.stage,
                        "review": evidence,
                    },
                ))
        self.store.write(rows)
