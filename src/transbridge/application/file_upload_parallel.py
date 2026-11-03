"""Bounded independent file pipelines with owner-thread checks and ordered receipts."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import replace
from pathlib import PurePosixPath
from queue import Empty, Queue
import re
from threading import Event, Lock

from transbridge.application.categorized_upload.diagnostics import log_event, log_failure
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.paratranz.file_transport import FileRequestAdmission


class UploadInventory:
    """Serialize 404 refreshes while retaining independent confirmed creations."""

    def __init__(self, inventory):
        self._values, self._created = dict(inventory), {}
        self._lock, self._refresh = Lock(), Lock()

    def get(self, path):
        with self._lock:
            return self._values.get(path)

    def __setitem__(self, path, file_id):
        with self._lock:
            self._values[path] = self._created[path] = file_id

    def relocate(self, port, project_id, path):
        with self._refresh:
            with self._lock:
                self._created.pop(path, None)
            fresh = port.inventory(project_id)
            with self._lock:
                self._values = {**fresh, **self._created}


class UploadChecks:
    """Run every contextual check on the owning worker, including phase checks."""

    def __init__(self, check_current, cancelled):
        self.check_current, self.cancelled = check_current, cancelled
        self.stopped, self.queue = Event(), Queue(maxsize=5)
        self.reason = "账号权限或请求限流未恢复，后续文件未上传。"

    def request(self):
        if self.stopped.is_set() or self.cancelled():
            raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "本轮已停止，未发起后续文件请求。")
        ready, errors = Event(), []
        self.queue.put((ready, errors))
        while not ready.wait(0.05):
            if self.stopped.is_set() or self.cancelled():
                raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "本轮已停止，未发起后续文件请求。")
        if errors:
            raise errors[0]

    def drain(self):
        while True:
            try:
                ready, errors = self.queue.get_nowait()
            except Empty:
                return
            try:
                if self.stopped.is_set() or self.cancelled():
                    raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "本轮已停止，未发起后续文件请求。")
                self.check_current()
            except Exception as exc:
                errors.append(exc)
                if not self.stopped.is_set() and not self.cancelled():
                    self.reason = f"本地工程或目标已变化，后续文件未上传：{exc}"
                self.stopped.set()
            finally:
                ready.set()


def publish_files_parallel(project_id, files, port, inventory, mode, cancelled, check_current, progress):
    from .file_upload import FileReceipt, FileUploadReceipt, _publish_parts, wire_records

    checks = UploadChecks(check_current, cancelled)
    inventory = UploadInventory(inventory)
    admission = FileRequestAdmission(port.cancellation, checks.stopped)
    # A.json and possible A_1.json splits share a scheduling group. One pipeline
    # per group runs at a time, in input order, avoiding overlapping split writes.
    groups = {file.path: re.sub(r"(?:_\d+)+$", "", str(PurePosixPath(file.path).with_suffix(""))) for file in files}

    def stopped_receipt(file):
        status = "cancelled" if cancelled() else "skipped"
        return (FileReceipt(file.path, status, "已取消后续文件。" if cancelled() else checks.reason),), (file,)

    def publish(file):
        try:
            if cancelled() or checks.stopped.is_set():
                return stopped_receipt(file)
            checks.request()
            try:
                records = wire_records(file)
            except (TypeError, ValueError) as exc:
                log_failure("file_upload_prepare", exc, path=file.path)
                return (FileReceipt(file.path, "failed", str(exc)),), (file,)
            child = port.fork(admission=admission, on_global_failure=lambda _error: checks.stopped.set())
            retry_files = []
            try:
                parts = _publish_parts(
                    project_id, file, records, inventory, child, mode, cancelled, checks.request, retry_files
                )
            finally:
                child.close()
            if any(item.http_status in {401, 403, 429} for item in parts):
                checks.stopped.set()
            if checks.stopped.is_set() and not cancelled():
                parts = tuple(
                    replace(item, status="skipped", message=checks.reason)
                    if item.status == "cancelled" and not item.source_applied and not item.translation_applied
                    else item
                    for item in parts
                )
            return parts, tuple(retry_files)
        except Exception as exc:
            log_failure("file_upload_target", exc, path=file.path)
            return (
                stopped_receipt(file)
                if checks.stopped.is_set() or cancelled()
                else ((FileReceipt(file.path, "failed", str(exc)),), (file,))
            )

    executor = ThreadPoolExecutor(max_workers=min(5, port.max_workers), thread_name_prefix="paratranz-upload")
    pending, results, waiting, active_groups = {}, {}, list(enumerate(files)), set()
    try:
        while pending or waiting:
            checks.drain()
            while waiting and len(pending) < port.max_workers and not checks.stopped.is_set():
                if cancelled():
                    break
                selected = next((row for row in waiting if groups[row[1].path] not in active_groups), None)
                if selected is None:
                    break
                index, file = selected
                waiting.remove(selected)
                active_groups.add(groups[file.path])
                pending[executor.submit(copy_context().run, publish, files[index])] = index
            if not pending:
                break
            done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                active_groups.remove(groups[files[index].path])
                parts, retries = results[index] = future.result()
                for item in parts:
                    log_event(
                        "file_upload_result",
                        path=item.path,
                        status=item.status,
                        source_applied=item.source_applied,
                        translation_applied=item.translation_applied,
                    )
                if progress:
                    progress(len(results), len(files), f"{files[index].path}：{parts[-1].message}")
        for index, file in waiting:
            results[index] = stopped_receipt(file)
    finally:
        checks.stopped.set()
        executor.shutdown(wait=True, cancel_futures=True)
    return FileUploadReceipt(
        tuple(item for index in range(len(files)) for item in results[index][0]),
        tuple(item for index in range(len(files)) for item in results[index][1]),
    )
