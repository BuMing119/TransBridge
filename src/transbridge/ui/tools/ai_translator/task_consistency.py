"""Reject combining attempts from different execution configurations or terminology."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock


def _value(value):
    if is_dataclass(value):
        return _value({
            field.name: getattr(value, field.name) for field in fields(value) if not field.name.startswith("_")
        })
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items() if key != "config_revision"}
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    if hasattr(value, "to_dict"):
        return _value(value.to_dict())
    if hasattr(value, "__dict__"):
        return _value({key: item for key, item in vars(value).items() if not key.startswith("_")})
    return value


def _digest(value):
    # Only the digest stays in memory; credentials and terms never enter records.
    return sha256(json.dumps(_value(value), sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class TaskConsistency:
    def __init__(self, request, *, config_provider=None, terminology_provider=None):
        self._config_provider = config_provider
        self._terminology_provider = terminology_provider
        self._request = self._request_identity(request)
        self._preferences = _digest(config_provider()) if config_provider else None
        self._terminology = self._term_identity(terminology_provider()) if terminology_provider else None
        self._paths = tuple(
            Path(path)
            for name in ("local_json_path", "local_csv_path", "local_excel_path")
            if (path := getattr(request.config, name, ""))
        )
        self._files = self._file_versions()
        self._loaded_terms = {}
        self._generated_terms = {}
        self._dynamic_files = {}
        self._lock = Lock()
        self.error = ""
        binding = getattr(request, "terminology_binding", None)
        if terminology_provider and binding is not None:
            if self._term_identity(binding.snapshot_ref) != self._terminology:
                self.error = "术语版本已变化，请新建任务"

    @classmethod
    def capture(cls, request, ctx):
        from transbridge.ai_translator.project_terminology_runtime import freeze_project_terminology
        from transbridge.paratranz.config_manager import LLMConfig

        return cls(
            request,
            config_provider=LLMConfig.load_from_file,
            terminology_provider=lambda: freeze_project_terminology(ctx).snapshot_ref,
        )

    @staticmethod
    def _request_identity(request):
        return _digest((
            request.run_id,
            request.config,
            request.spec,
            getattr(request, "terminology_binding", None)
            and getattr(request.terminology_binding, "snapshot_ref", None),
        ))

    @staticmethod
    def _term_identity(ref):
        return None if ref is None else ref.snapshot_identity

    def _file_versions(self):
        return tuple(self._file_version(path) for path in self._paths)

    @staticmethod
    def _file_version(path):
        if path.exists():
            stat = path.stat()
            return str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        return str(path.resolve()), None

    def require_current(self, request):
        if not self.error:
            if self._request_identity(request) != self._request:
                self.error = "任务配置已变化，请新建任务"
            elif self._config_provider and _digest(self._config_provider()) != self._preferences:
                self.error = "AI 配置已变化，请新建任务"
            elif self._terminology_provider and self._term_identity(self._terminology_provider()) != self._terminology:
                self.error = "术语版本已变化，请新建任务"
            elif self._file_versions() != self._files:
                self.error = "术语文件已变化，请新建任务"
            else:
                with self._lock:
                    if any(self._file_version(path) != version for path, version in self._dynamic_files.items()):
                        self.error = "动态术语已变化，请新建任务"
        if self.error:
            raise RuntimeError(self.error)

    def observe_terms(self, source, stage, manager):
        """Called on the worker after loading, before sending any model request.

        Legacy/remote inputs have no revision API. Compare the actual loaded
        values against the first attempt rather than trusting a cache filename.
        """
        current = {row["term"]: row for item in manager._merged_terms if (row := self._term_row(item))}
        version = getattr(manager, "_vector_snapshot_identity", None)
        dynamic_path = getattr(getattr(manager, "_dynamic_db", None), "_path", None)
        with self._lock:
            if dynamic_path:
                path = Path(dynamic_path)
                signature = self._file_version(path)
                if self._dynamic_files.setdefault(path, signature) != signature:
                    self.error = "动态术语已变化，请新建任务"
            # Translation and proofreading must share the same source baseline,
            # including when a stage is reached for the first time on a retry.
            baseline, previous_version = self._loaded_terms.setdefault(source, (current, version))
            own = self._generated_terms.get(source, {})
            changed = (
                any(
                    current.get(term) != baseline.get(term) and current.get(term) != own.get(term)
                    for term in current.keys() | baseline.keys()
                )
                or not baseline.keys() <= current.keys()
            )
            if changed or version != previous_version:
                self.error = "术语内容已变化，请新建任务"
            if self.error:
                raise RuntimeError(self.error)

    @staticmethod
    def _term_row(entry):
        row = _value(entry)
        row.pop("created_at", None)
        return row

    def record_generated_terms(self, source, manager, terms):
        """Only acknowledge the exact dynamic values this task successfully wrote."""
        requested = {term: (translation, origin, context) for term, translation, origin, context in terms}
        generated = {}
        for entry in manager.get_dynamic_db().as_list():
            if requested.get(entry.term) == (entry.translation, entry.source, entry.context):
                generated[entry.term] = self._term_row(entry)
        with self._lock:
            self._generated_terms.setdefault(source, {}).update(generated)
            path = Path(manager.get_dynamic_db()._path)
            self._dynamic_files[path] = self._file_version(path)
