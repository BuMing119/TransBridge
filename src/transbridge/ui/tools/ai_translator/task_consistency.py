"""Keep execution settings stable and protect generated-term writes separately."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from transbridge.ai_translator.term_formats import load_terms_json
from transbridge.ai_translator.term_publication_conflict import TermPublicationConflict


class TaskConsistency:
    def __init__(self, request):
        from .task_references import reference_store_for_request

        self._request = self._request_identity(request)
        self.reference_store = reference_store_for_request(request)
        self._dynamic_files = {}
        self._lock = RLock()
        self.error = ""

    @classmethod
    def capture(cls, request, ctx):
        return cls(request)

    @staticmethod
    def _request_identity(request):
        from .run_controller import TranslationRunRequest

        reference = getattr(request, "terminology_binding", None)
        snapshot = None if reference is None else getattr(reference, "snapshot_ref", None)
        if not isinstance(request, TranslationRunRequest):
            raise TypeError("Task consistency requires the frozen admitted AI request")
        return request.spec, request.config_snapshot, snapshot

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
