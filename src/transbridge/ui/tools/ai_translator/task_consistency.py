"""Keep execution settings stable and protect generated-term writes separately."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock

from transbridge.ai_translator.term_formats import load_terms_json
from transbridge.ai_translator.term_publication_conflict import TermPublicationConflict


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
        from .task_references import reference_store_for_request

        self._config_provider = config_provider
        self._request = self._request_identity(request)
        self._preferences = _digest(config_provider()) if config_provider else None
        self.reference_store = reference_store_for_request(request)
        self._dynamic_files = {}
        self._lock = RLock()
        self.error = ""
        # Kept as an optional compatibility argument. Live reference providers
        # must never be queried as a task validity or application condition.

    @classmethod
    def capture(cls, request, ctx):
        from transbridge.paratranz.config_manager import LLMConfig

        return cls(request, config_provider=LLMConfig.load_from_file)

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
    def _file_version(path):
        if path.exists():
            stat = path.stat()
            return str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        return str(path.resolve()), None

    def require_current(self, request):
        with self._lock:
            if not self.error:
                if self._request_identity(request) != self._request:
                    self.error = "任务配置已变化，请新建任务"
                elif self._config_provider and _digest(self._config_provider()) != self._preferences:
                    self.error = "AI 配置已变化，请新建任务"
            if self.error:
                raise RuntimeError(self.error)

    def _require_writable_terms(self):
        if self.error:
            raise RuntimeError(self.error)
        for path, version in self._dynamic_files.items():
            if self._file_version(path) != version:
                raise TermPublicationConflict(f"生成术语文件已被其他操作修改，跳过共享写入：{path}")

    @contextmanager
    def terminology_access(self):
        """Serialize task views without comparing reference files to live state."""
        with self._lock:
            yield

    @contextmanager
    def terminology_write_access(self):
        """Reject conflicting publication without invalidating translation results."""
        with self._lock:
            self._require_writable_terms()
            yield

    def observe_terms(self, source, stage, manager):
        """Register only writable resources; reference content is never a gate."""
        with self._lock:
            dynamic_path = getattr(getattr(manager, "_dynamic_db", None), "_path", None)
            if dynamic_path:
                path = Path(dynamic_path).resolve()
                self._dynamic_files.setdefault(path, self._file_version(path))

    def record_generated_terms(self, source, manager, terms):
        """Only acknowledge the exact dynamic values this task successfully wrote."""
        with self._lock:
            if self.error:
                raise RuntimeError(self.error)
            database = manager.get_dynamic_db()
            pool = getattr(manager, "_shared_term_pool", None)
            path = Path(database._path).resolve()
            version = self._file_version(path)
            saved = database.as_list()
            if pool is not None:
                # Check the envelope as well as term rows. Corruption and real
                # IO failures propagate; external edits are typed conflicts.
                pool.load()
            persisted = load_terms_json(path, source=None)
            # Another writer may have changed the file after our save. Never
            # acknowledge its contents or signature as this task's own update.
            if self._file_version(path) != version or persisted != saved:
                raise TermPublicationConflict(f"生成术语保存确认时检测到外部写入，保留外部内容：{path}")
            self._dynamic_files[path] = version
