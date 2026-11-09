# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""A bounded file lookahead; source validation and publication stay with the owner."""

from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
from queue import Empty, SimpleQueue
from threading import Event, Lock

from .file_transport import FileRequestAdmission


class ParallelFileReads:
    """Keep at most five response futures, consumed in authoritative source order.

    A 404 refresh still runs on the owning worker; its new file IDs bypass stale
    lookahead entries. Parsed source caches remain owned by the existing reader.
    """

    def __init__(self, gateway, project_id, inventory, select_files, *, limit, on_download=None):
        self.gateway, self.project_id, self.limit = gateway, project_id, limit
        self.cancellation = gateway.cancellation
        self.max_workers = min(5, gateway.max_workers)
        self._stopped, self._lock = Event(), Lock()
        self._error = None
        self._inventory, self._select_files = dict(inventory), select_files
        self._seen, self._finished = set(), set()
        self._pending = {}
        self._downloads = SimpleQueue()
        self._on_download = on_download
        self._start()

    def _start(self):
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="paratranz-download")
        self._stopped = Event()
        self._admission = FileRequestAdmission(self.cancellation, self._stopped)
        self._ids = iter(
            dict.fromkeys(
                (source_id, file_id)
                for source_id, file_id in self._select_files(self._inventory)
                if source_id not in self._finished and file_id not in self._seen
            )
        )
        try:
            self._fill()
        except Exception:
            self.close()
            raise

    def _fail(self, error):
        with self._lock:
            if self._error is None:
                self._error = error
        self._stopped.set()

    def _raise_global(self):
        self.cancellation.raise_if_cancelled()
        if self._error is not None:
            raise self._error

    def _read(self, file_id):
        child = self.gateway.fork(admission=self._admission, on_global_failure=self._fail)
        try:
            rows = child.get_file_translation(self.project_id, file_id)
            self._downloads.put(file_id)
            if isinstance(rows, list) and len(rows) > self.limit:
                raise ValueError("单个云端文件超过本轮完整下载上限，未采用该插件的部分内容。")
            return rows
        finally:
            child.close()

    def _fill(self):
        while len(self._pending) < self.max_workers and not self._stopped.is_set():
            self._raise_global()
            row = next(self._ids, None)
            if row is None:
                break
            source_id, file_id = row
            if source_id in self._finished or file_id in self._pending:
                continue
            self._pending[file_id] = (source_id, self._executor.submit(copy_context().run, self._read, file_id))

    def inventory(self, project_id):
        self._raise_global()
        # No old-generation future survives a layout refresh. The owner keeps
        # already parsed (path, ID) entries in its ordinary source cache.
        self.close()
        self._raise_global()
        fresh = self.gateway.inventory(project_id)
        self._inventory = dict(fresh)
        self._start()
        return fresh

    def get_file_translation(self, project_id, file_id):
        self._raise_global()
        if project_id != self.project_id:
            raise ValueError("本轮下载不能切换云端项目。")
        scheduled = self._pending.pop(file_id, None)
        try:
            if scheduled is not None:
                while not wait((scheduled[1],), timeout=0.1).done:
                    self._report_downloads()
                    self._raise_global()
            rows = self._read(file_id) if scheduled is None else scheduled[1].result()
            self._raise_global()
            self._seen.add(file_id)
            return rows
        finally:
            self._report_downloads()
            # Refill after consumption, keeping futures/responses bounded even
            # when the server completes later files before the first file.
            if not self._stopped.is_set():
                self._fill()

    def close(self):
        self._stopped.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._report_downloads()
        self._pending.clear()

    def _report_downloads(self):
        while True:
            try:
                file_id = self._downloads.get_nowait()
            except Empty:
                return
            if self._on_download is not None:
                self._on_download(file_id)

    def finish_source(self, source_id):
        self._finished.add(source_id)
        if any(owner == source_id for owner, _future in self._pending.values()):
            # Drop a failed source's unconsumed lookahead without retaining a
            # response prefix forever or starving the following sources.
            self.close()
            self._raise_global()
            self._start()
