"""Serial, rate-budgeted file API gateway for ordinary and categorized uploads."""

from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import json
from pathlib import PurePosixPath
from threading import Event, Lock
from time import monotonic

from transbridge.application.categorized_upload.diagnostics import log_event
from transbridge.application.file_upload import FileWriteResult
from transbridge.application.ports.paratranz import ExternalServiceError

from .api.paratranz_files_api import ParatranzFilesAPI
from .categorized_upload import UploadCancellation, _complete_files, _file_path
from .paratranz_client import RetryPolicy


class RequestBudget:
    """One shared pacing budget per endpoint/credential, including reads/retries."""

    def __init__(self, interval=1.0):
        self.interval = interval
        self._lock = Lock()
        self._next = {}
        self._active = {}

    @contextmanager
    def slot(self, key, cancellation):
        with self._lock:
            active = self._active.setdefault(key, Lock())
        while not active.acquire(blocking=False):
            cancellation.raise_if_cancelled()
            cancellation.wait(0.1)
        try:
            self.acquire(key, cancellation)
            yield
        finally:
            active.release()

    def acquire(self, key, cancellation):
        while True:
            cancellation.raise_if_cancelled()
            with self._lock:
                now = monotonic()
                delay = self._next.get(key, now) - now
                if delay <= 0:
                    self._next[key] = now + self.interval
                    return
            cancellation.wait(delay)


_SHARED_BUDGET = RequestBudget()


class FilePublicationGateway(ParatranzFilesAPI):
    """Trust file import responses; never fetch strings or persist their identities.

    Official global ceiling is 480 requests/minute/user (OpenAPI 0.5.7).
    This gateway conservatively paces at 1 request/second and retries only explicit
    429 rejections, respecting Retry-After. Ambiguous POST failures are not replayed.
    """

    def __init__(self, config, *, cancel=None, status=None, session=None, budget=None):
        super().__init__(config=config, session=session, retry_policy=RetryPolicy(max_attempts=1))
        self.cancellation = UploadCancellation(cancel or Event())
        self._budget = budget or _SHARED_BUDGET
        account = getattr(config, "user_id", None) or hashlib.sha256((config.token or "").encode()).hexdigest()
        self._budget_key = (config.base_url, account)
        self._status = status or (lambda _message: None)

    def _request(self, method, endpoint, **kwargs):
        cancellation = kwargs.pop("cancellation", None) or self.cancellation
        for attempt in range(3):
            with self._budget.slot(self._budget_key, cancellation):
                started = monotonic()
                try:
                    result = super()._request(method, endpoint, cancellation=cancellation, **kwargs)
                    log_event(
                        "file_http_response",
                        method=method,
                        endpoint=endpoint,
                        elapsed_ms=round((monotonic() - started) * 1000),
                        status=(
                            result.get("status")
                            if isinstance(result, Mapping)
                            and isinstance(result.get("status"), str)
                            and result.get("status") in {"hashMatched", "unchanged", "empty"}
                            else None
                        ),
                    )
                    return result
                except ExternalServiceError as exc:
                    if exc.status != 429 or attempt == 2:
                        raise
                    delay = exc.retry_after if exc.retry_after is not None else 2 ** (attempt + 1)
                    self._status(f"ParaTranz 请求限流，等待 {delay:g} 秒…")
                    cancellation.wait(delay)
                    cancellation.raise_if_cancelled()
        raise RuntimeError("unreachable rate-limit retry state")

    def inventory(self, project_id):
        files = _complete_files(self.list_files(project_id))
        result = {}
        ids = set()
        for file in files:
            path, file_id = _file_path(file), file.get("id")
            if path in result or type(file_id) is not int or file_id < 1 or file_id in ids:
                raise ValueError("云端文件列表存在重复路径或无效文件 ID。")
            result[path] = file_id
            ids.add(file_id)
        return result

    def create(self, project_id, path, records):
        target = PurePosixPath(path)
        content = json.dumps(records, ensure_ascii=False, allow_nan=False).encode("utf-8")
        result = self.upload_file_bytes(
            project_id, target.name, content, path="" if str(target.parent) == "." else str(target.parent) + "/"
        )
        return _write_result(result, require_id=True)

    def update(self, project_id, file_id, path, records):
        content = json.dumps(records, ensure_ascii=False, allow_nan=False).encode("utf-8")
        result = self.update_file_bytes(project_id, file_id, PurePosixPath(path).name, content, incremental=True)
        return _write_result(result)

    def translate(self, project_id, file_id, path, records, *, force):
        items = tuple({field: record[field] for field in ("key", "translation", "stage")} for record in records)
        result = self.import_translations(project_id, file_id, PurePosixPath(path).name, items, force=force)
        return _write_result(result)


def _write_result(payload, *, require_id=False):
    if not isinstance(payload, Mapping):
        raise ValueError("ParaTranz 未返回文件操作结果。")
    status = payload.get("status")
    if status == "empty":
        raise ValueError("ParaTranz 返回 empty，未导入任何内容。")
    if status in {"hashMatched", "unchanged"}:
        if require_id:
            raise ValueError("创建文件未返回新文件身份。")
        return FileWriteResult(status)
    file = payload.get("file", payload)
    file_id = file.get("id") if isinstance(file, Mapping) else None
    if type(file_id) is not int or file_id < 1:
        raise ValueError("ParaTranz 文件操作响应缺少有效文件 ID，结果待核实。")
    return FileWriteResult(file_id=file_id)
