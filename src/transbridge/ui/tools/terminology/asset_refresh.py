"""Coalesce asset reads while preserving the latest completion of each workload."""

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer

from .asset_snapshot import read_assets
from .async_actions import AsyncActionRunner


@dataclass(frozen=True, slots=True)
class AssetRefreshResult:
    assets: object
    completions: tuple[tuple[object, object], ...]


class AssetRefreshCoordinator(QObject):
    """One active read and one merged follow-up; historical replay stays bounded."""

    def __init__(self, parent, commands, context, *, browsing=None, on_ready, on_error, thread_pool=None):
        super().__init__(parent)
        self._commands, self._context, self._browsing = commands, context, browsing
        self._ready, self._error = on_ready, on_error
        self._runner = AsyncActionRunner(self, on_error=self._failed, thread_pool=thread_pool)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)
        self._pending = {}
        self._active_completions = {}
        self._seen = set()
        self._dirty = self._active = self._closed = False
        self._cancellation = None

    def request(self, workload=None, run_id=None):
        if self._closed or (run_id is not None and run_id in self._seen):
            return
        if run_id is not None:
            self._seen.add(run_id)
            self._pending.pop(workload, None)
            self._pending[workload] = run_id
        self._dirty = True
        if self._active:
            self._cancellation.cancel()
        else:
            self._timer.start(0)

    def close(self):
        self._closed = True
        self._timer.stop()
        self._runner.close()
        self._pending.clear()
        self._active_completions.clear()

    def _start(self):
        if self._closed or self._active or not self._dirty:
            return
        self._active, self._dirty = True, False
        self._active_completions, self._pending = self._pending, {}
        completions = tuple(self._active_completions.items())
        self._cancellation = self._runner.run_read(
            lambda token: self._read(completions, token), self._finished, on_cancelled=self._follow_up
        )

    def _read(self, completions, token):
        assets = read_assets(self._commands, self._context, browsing=self._browsing, cancellation=token)
        results = []
        read = getattr(self._commands, "task_result", lambda *_args: None)
        for workload, run_id in completions:
            token.raise_if_cancelled()
            results.append((workload, read(run_id, self._context)))
        token.raise_if_cancelled()
        return AssetRefreshResult(assets, tuple(results))

    def _follow_up(self):
        self._active = False
        if self._dirty:
            # A later completion of the same workload wins, but other workload
            # feedback must survive a read superseded by an unrelated refresh.
            merged = dict(self._active_completions)
            for workload, run_id in self._pending.items():
                merged.pop(workload, None)
                merged[workload] = run_id
            self._pending = merged
            self._timer.start(0)
        self._active_completions = {}
        return self._dirty

    def _finished(self, result):
        if not self._closed and not self._follow_up():
            self._ready(result)

    def _failed(self, error):
        if not self._closed and not self._follow_up():
            self._error(error)
