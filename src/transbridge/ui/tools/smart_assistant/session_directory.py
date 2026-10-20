"""Read session-directory metadata off Qt and publish only the latest complete snapshot."""

from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


def scope_labels(project_id, variant_id):
    return {
        "project_name": project_id or "未归属对话",
        "variant_name": variant_id or "未绑定版本",
        "available": bool(project_id and variant_id),
    }


def _read_directory(source, lookup):
    rows = [dict(row) for row in source.list_sessions()]
    labels = {}
    for row in rows:
        key = (row.get("project_id"), row.get("variant_id"))
        if key not in labels:
            labels[key] = dict(lookup(*key) if lookup is not None else scope_labels(*key))
        row.update({name: value for name, value in labels[key].items() if name in {"project_name", "variant_name"}})
    return rows, labels


class SessionDirectory(QObject):
    """A display cache, never an authority for Session activation or execution.

    The owned timer collects worker results on Qt. Workers never capture a QObject
    callback, so destruction safely discards any in-flight read without waiting.
    """

    updated = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self._labels = {}
        self._generation = 0
        self._running_generation = 0
        self._requested = None
        self._future = None
        self._closed = False
        self._reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-directory")
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._collect)

    @property
    def pending(self):
        return not self._closed and self._future is not None

    def refresh(self, source, lookup=None):
        if self._closed:
            return
        self._generation += 1
        self._requested = source, lookup
        if self._future is None:
            self._start()

    def labels(self, values, *, lookup_available):
        key = values.get("project_id"), values.get("variant_id")
        if key in self._labels:
            return dict(self._labels[key])
        labels = scope_labels(*key)
        if lookup_available and all(key):
            labels.update(available=False, reason="工程信息仍在加载，请稍后重试。")
        return labels

    def _start(self):
        self._running_generation = self._generation
        self._future = self._reader.submit(_read_directory, *self._requested)
        self._timer.start()

    def _collect(self):
        if self._closed or self._future is None or not self._future.done():
            return
        future, self._future = self._future, None
        self._timer.stop()
        if self._running_generation != self._generation:
            self._start()
            return
        try:
            rows, labels = future.result()
        except Exception as exc:
            self.failed.emit(exc)
            return
        self.rows, self._labels = rows, labels
        self.updated.emit(rows)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._reader.shutdown(wait=False, cancel_futures=True)
