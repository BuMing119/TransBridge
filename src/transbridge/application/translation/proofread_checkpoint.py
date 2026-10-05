"""Atomic, project-scoped proofreading candidates, independent of task reports."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from .postprocess import PostProcessCandidate
from .protected_syntax import protected_syntax_matches


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


def _input(candidate: PostProcessCandidate) -> str:
    return _digest({
        "key": candidate.entry_key.to_dict(),
        "revision": candidate.before_revision.value,
        "original": candidate.original,
        "before": candidate.before_text,
        "context": candidate.context,
        "stage": candidate.stage,
    })


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

    def saved_count(self) -> int:
        """Historical well-formed candidates; not a promise of validity for current inputs."""
        if not self.path.exists():
            return 0
        try:
            with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
                return db.execute(
                    "SELECT COUNT(*) FROM candidates WHERE CASE WHEN json_valid(payload) THEN "
                    "json_type(payload, '$.text') = 'text' AND trim(json_extract(payload, '$.text')) != '' "
                    "AND json_type(payload, '$.terms') = 'text' "
                    "AND length(json_extract(payload, '$.terms')) = 64 "
                    "AND json_extract(payload, '$.terms') NOT GLOB '*[^0-9a-f]*' ELSE 0 END"
                ).fetchone()[0]
        except sqlite3.Error as exc:
            raise RuntimeError(f"无法统计校对断点 {self.path}：{exc}") from exc

    def read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
                rows = {
                    key: json.loads(payload) for key, payload in db.execute("SELECT input, payload FROM candidates")
                }
                if any(not isinstance(row, dict) for row in rows.values()):
                    raise ValueError("校对断点内容格式无效")
                return rows
        except (sqlite3.Error, ValueError) as exc:
            raise RuntimeError(f"无法读取校对断点 {self.path}：{exc}") from exc

    def write(self, rows: list[tuple[str, dict]]) -> None:
        if not rows:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS candidates (input TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                db.executemany(
                    "INSERT OR REPLACE INTO candidates VALUES (?, ?)",
                    [(key, json.dumps(value, ensure_ascii=False)) for key, value in rows],
                )
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(f"无法保存校对断点 {self.path}：{exc}") from exc

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
            if row is None:
                continue
            terms = dict(resolver(candidate))
            text = row.get("text")
            if row.get("terms") != _digest(terms):
                continue
            if not isinstance(text, str) or not text.strip() or not protected_syntax_matches(candidate.original, text):
                continue
            # Both first-pass and refined candidates re-enter deterministic closure.
            restored[candidate.entry_key] = replace(candidate, text=text, phases=("proofread",), accepted=True)
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
                rows.append((_input(original), {"text": candidate.text, "terms": _digest(terms)}))
        self.store.write(rows)
