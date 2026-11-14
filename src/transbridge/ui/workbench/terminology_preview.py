# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Capture immutable entry batches and compute one isolated terminology preview."""

from __future__ import annotations

from threading import Event
from time import perf_counter
from types import MappingProxyType
from weakref import finalize

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal

from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput, ProfileEntryProjector
from transbridge.application.terminology_profiles.projection import TerminologyProfileProjector
from transbridge.application.terminology_profiles.transition_projection import same_profile_revision

from .terminology_alignment import AlignmentConsentRequired, AlignmentPreviewCall, PreviewAlignmentCancellation


def profile_snapshot_key(profile, base, source_profile=None):
    profile_key = (
        None
        if profile is None
        else (profile.project_id, profile.profile_id, profile.revision, profile.content_digest, profile.name)
    )
    base_key = None if base is None else (base.snapshot_identity, base.status, base.diagnostics)
    source_key = (
        None
        if source_profile is None
        else (
            source_profile.project_id,
            source_profile.profile_id,
            source_profile.revision,
            source_profile.content_digest,
        )
    )
    return profile_key, base_key, source_key


def preview_identity(ctx, scope):
    return scope, ctx.active_version_identity, ctx.project_revision, ctx.variant_revision


class _Signals(QObject):
    finished = pyqtSignal(int, object, object)
    aligned = pyqtSignal(int, object, object)
    progress = pyqtSignal(int, int, int)


class _ProjectionCall(QRunnable):
    def __init__(
        self,
        generation,
        profile,
        base,
        chunks,
        prepared,
        cancelled,
        closed,
        signals,
        source_profile=None,
        resolver=None,
        cache=None,
    ):
        super().__init__()
        self.generation, self.profile, self.base = generation, profile, base
        self.chunks, self.prepared = chunks, prepared
        self.cancelled, self.closed, self.signals = cancelled, closed, signals
        self.source_profile = source_profile
        self.resolver = resolver
        self.cache = cache

    def run(self):
        if self.cancelled.is_set() or self.closed.is_set():
            return
        result, error = None, None
        try:
            if self.cache is not None:
                from transbridge.application.terminology_profiles.profile_alignment import align_profile_entries

                resolver, output = align_profile_entries(
                    (entry for chunk in self.chunks for entry in chunk),
                    self.profile,
                    self.base,
                    source_profile=self.source_profile,
                    service=None,
                    cache=self.cache,
                    allow_model=False,
                    cancellation=PreviewAlignmentCancellation(self.cancelled, self.closed),
                )
                projector = ProfileEntryProjector(
                    self.profile,
                    self.base,
                    source_profile=self.source_profile,
                    projector=TerminologyProfileProjector(resolver=resolver),
                )
                result = projector, output, resolver
                if not self.cancelled.is_set() and not self.closed.is_set():
                    self.signals.finished.emit(self.generation, result, None)
                return
            projector = self.prepared or ProfileEntryProjector(
                self.profile,
                self.base,
                source_profile=self.source_profile,
                projector=TerminologyProfileProjector(resolver=self.resolver),
            )
            output = {}
            for chunk in self.chunks:
                for entry in chunk:
                    if self.cancelled.is_set() or self.closed.is_set():
                        return
                    output[entry.entry_key] = projector.project_input(entry)
            result = projector, MappingProxyType(output)
        except Exception as exc:  # noqa: BLE001 - retain the failure for the owning UI
            error = exc
        if not self.cancelled.is_set() and not self.closed.is_set():
            self.signals.finished.emit(self.generation, result, error)


class TerminologyPreviewCoordinator(QObject):
    """Own profile identity, bounded capture, cancellation and atomic display results."""

    changed = pyqtSignal()
    alignment_status_changed = pyqtSignal()
    alignment_consent_required = pyqtSignal()

    def __init__(
        self,
        parent,
        *,
        identity,
        thread_pool=None,
        batch_size=256,
        capture_budget_ms=4,
        alignment_prepare=None,
        alignment_identity=None,
    ):
        super().__init__(parent)
        self._identity = identity
        self._alignment_identity = alignment_identity
        self._request_identity_source = identity
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._batch_size = batch_size
        self._capture_budget = capture_budget_ms / 1000
        self._closed, self._cancelled = Event(), Event()
        parent.destroyed.connect(finalize(parent, self._closed.set))
        self._signals = _Signals()
        self._signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._signals.aligned.connect(self._aligned, Qt.ConnectionType.QueuedConnection)
        self._signals.progress.connect(self._alignment_progress, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._capture_batch)
        self._generation = 0
        self._entries = ()
        self._capture_entries = ()
        self._deferred_alignment = ()
        self._chunks = []
        self._offset = 0
        self._request_identity = None
        self._key = profile_snapshot_key(None, None)
        self._prepared = None
        self._alignment_prepare = alignment_prepare
        self._alignment_resolver = None
        self._cache_provider = None
        self._cache = None
        self._retry_uncertain = True
        self.alignment_uses_model = True
        self._require_alignment_consent = False
        self.alignment_running = False
        self.alignment_message = ""
        self.profile = self.base = self.source_profile = None
        self.projection = MappingProxyType({})
        self.pending = False
        self.error = ""
        self._window = None
        self._closing_owner = None
        parent.installEventFilter(self)
        self._watch_window()

    @property
    def message(self):
        if self.alignment_message:
            return self.alignment_message
        if self.error:
            return f"译名方案预览失败：{self.error}。当前保留项目译文，请重新选择方案重试。"
        return "正在生成译名方案预览；暂时显示项目译文。" if self.pending else ""

    def set_profile(self, profile, base=None, *, source_profile=None):
        key = profile_snapshot_key(profile, base, source_profile)
        if self._closed.is_set() or (key == self._key and not self.error):
            return False
        self._key, self.profile, self.base = key, profile, base
        self.source_profile = source_profile
        self._prepared = None
        self._start()
        return True

    def set_entries(self, entries, *, viewport_only=False):
        # The owner replaces this list on collection refresh. Mutable entry fields
        # are copied in bounded main-thread batches, never read by the worker.
        self._entries = entries
        if (viewport_only or self._alignment_identity is not None) and self.alignment_running and self._is_current():
            return
        self._start()

    def set_cache_provider(self, provider):
        """Capture the active Variant's cache on Qt, before any worker is submitted."""
        self._cache_provider = provider

    @property
    def cache_available(self):
        return self._cache is not None

    def clear_alignment_cache(self):
        cache = self._cache if self._cache_provider is None else self._cache_provider()
        if cache is not None:
            cache.clear_all()
        self._alignment_resolver = self._prepared = None
        self._start()
        self.changed.emit()

    def invalidate_context(self, *_args):
        self._alignment_resolver = None
        if self.set_profile(None):
            self.changed.emit()

    @property
    def profile_label(self):
        if same_profile_revision(self.source_profile, self.profile):
            return None
        return "项目默认译名" if self.profile is None else self.profile.name

    def start_alignment(
        self, *, entries=None, deferred=(), retry_uncertain=True, allow_model=True, require_consent=False
    ):
        entries = self._entries if entries is None else entries
        if self.pending or self.profile_label is None or not (entries or deferred) or self._closed.is_set():
            return False
        self._retry_uncertain = retry_uncertain
        self.alignment_uses_model = allow_model
        self._require_alignment_consent = require_consent
        self._start(alignment=True, entries=entries, deferred=deferred)
        self.alignment_status_changed.emit()
        return True

    def cancel_alignment(self):
        if not self.alignment_running:
            return
        self._cancelled.set()
        self._timer.stop()
        self._generation += 1
        self._chunks = []
        self._capture_entries = self._deferred_alignment = ()
        self.pending = self.alignment_running = False
        self.alignment_message = "AI 定位已取消，保留此前完整预览。"
        self.changed.emit()

    def capture_projector(self):
        if self.pending or not self._is_current():
            raise RuntimeError("译名方案预览尚未完成或正文已变化，请等待预览完成后再保存或导出。")
        return TerminologyProfileProjector(resolver=self._alignment_resolver)

    def _start(self, *, alignment=False, entries=None, deferred=()):
        self._cancelled.set()
        self._cancelled = Event()
        self._timer.stop()
        self._generation += 1
        cache = None if self._cache_provider is None else self._cache_provider()
        if cache is not self._cache:
            self._alignment_resolver = self._prepared = None
        self._cache = cache
        if not alignment:
            self.projection = MappingProxyType({})
        self.alignment_running = alignment
        self.alignment_message = "正在准备 AI 定位；保留此前完整预览。" if alignment else ""
        if alignment and not self.alignment_uses_model:
            self.alignment_message = "正在读取定位缓存；不请求 AI。"
        self.error = ""
        self._capture_entries = self._entries if entries is None else entries
        self._deferred_alignment = deferred
        self.pending = (
            self.profile_label is not None and bool(self._capture_entries or deferred) and not self._closed.is_set()
        )
        self._request_identity_source = (self._alignment_identity or self._identity) if alignment else self._identity
        self._request_identity = self._request_identity_source()
        self._chunks, self._offset = [], 0
        if self.pending:
            self._timer.start(0)

    def _is_current(self):
        if self._closing_owner is not None:
            self._close_if_hidden(self._closing_owner)
        return not self._closed.is_set() and self._request_identity == self._request_identity_source()

    def _discard_stale(self):
        self._cancelled.set()
        self._timer.stop()
        self._chunks = []
        self.pending = False
        self.alignment_running = False
        self.alignment_message = ""
        self._alignment_resolver = None
        if not self._closed.is_set():
            self.error = "条目上下文已变化，请刷新当前列表或重新选择译名方案"
            self.changed.emit()

    def _capture_batch(self):
        if not self._is_current():
            self._discard_stale()
            return
        chunk = []
        deadline = perf_counter() + self._capture_budget
        try:
            while self._offset < len(self._capture_entries) and len(chunk) < self._batch_size:
                chunk.append(ProfileEntryInput.capture(self._capture_entries[self._offset]))
                self._offset += 1
                if perf_counter() >= deadline:
                    break
        except Exception as exc:  # noqa: BLE001 - malformed input remains visible, with no partial application
            self._failed(exc)
            return
        self._chunks.append(tuple(chunk))
        if self._offset < len(self._capture_entries):
            self._timer.start(0)
            return
        if self.alignment_running:
            call = AlignmentPreviewCall(
                self._generation,
                self.profile,
                self.base,
                tuple(self._chunks),
                self.source_profile,
                self._alignment_resolver,
                PreviewAlignmentCancellation(self._cancelled, self._closed),
                self._signals,
                self._alignment_prepare,
                self._deferred_alignment,
                retry_uncertain=self._retry_uncertain,
                cache=self._cache,
                allow_model=self.alignment_uses_model,
                require_consent=self._require_alignment_consent,
            )
        else:
            call = _ProjectionCall(
                self._generation,
                self.profile,
                self.base,
                tuple(self._chunks),
                self._prepared,
                self._cancelled,
                self._closed,
                self._signals,
                self.source_profile,
                self._alignment_resolver,
                self._cache,
            )
        self._pool.start(call)
        self._chunks = []
        self._capture_entries = ()
        self._deferred_alignment = ()

    def _finished(self, generation, result, error):
        if generation != self._generation:
            return
        if not self._is_current():
            self._discard_stale()
            return
        if error is not None:
            self._failed(error)
            return
        self._prepared, self.projection = result[:2]
        if len(result) == 3:
            self._alignment_resolver = result[2]
        self.pending = False
        self.changed.emit()

    def _failed(self, error):
        self._cancelled.set()
        self._chunks = []
        self.pending = False
        self.alignment_running = False
        self.alignment_message = ""
        self.error = str(error)
        self.changed.emit()

    def _alignment_progress(self, generation, completed, total):
        if generation != self._generation or not self.alignment_running:
            return
        if not self._is_current():
            self._discard_stale()
            return
        self.alignment_message = f"AI 定位：{completed}/{total} 条；保留此前完整预览。"
        self.alignment_status_changed.emit()

    def _aligned(self, generation, result, error):
        if generation != self._generation or not self.alignment_running:
            return
        if not self._is_current():
            self._discard_stale()
            return
        self.pending = self.alignment_running = False
        if isinstance(error, AlignmentConsentRequired):
            self.alignment_message = str(error)
            self.changed.emit()
            self.alignment_consent_required.emit()
            return
        if error is not None:
            self.alignment_message = f"AI 定位未完成：{error}。保留此前完整预览，可重试。"
        else:
            self._alignment_resolver, projection = result
            self.projection = MappingProxyType(dict(projection))
            self._prepared = None
            unresolved = sum(bool(item.diagnostics) for item in self.projection.values())
            self.alignment_message = f"AI 定位完成；{unresolved} 条仍需确认，已保留原译文。"
            if not self.alignment_uses_model:
                self.alignment_message = f"定位缓存读取完成；{unresolved} 条仍需确认，已保留原译文。"
        self.changed.emit()

    def close(self):
        self._closed.set()
        self._cancelled.set()
        self._timer.stop()
        self._chunks = []
        self._capture_entries = self._deferred_alignment = ()
        self.pending = False
        self.alignment_running = False
        for owner in (self.parent(), self._window):
            if owner is not None and not sip.isdeleted(owner):
                owner.removeEventFilter(self)

    def _watch_window(self):
        window = self.parent().window()
        if window is not self._window:
            if self._window is not None and self._window is not self.parent() and not sip.isdeleted(self._window):
                self._window.removeEventFilter(self)
            self._window = window
            if window is not self.parent():
                window.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        closed = self.__dict__.get("_closed")
        if closed is None or closed.is_set():
            return False
        if event.type() == QEvent.Type.Close:
            self._closing_owner = watched
            QTimer.singleShot(0, lambda: self._close_if_hidden(watched))
        elif watched is self.parent() and event.type() in (QEvent.Type.ParentChange, QEvent.Type.Show):
            self._watch_window()
        return super().eventFilter(watched, event)

    def _close_if_hidden(self, owner):
        closed = self.__dict__.get("_closed")
        if closed is None:
            return
        if not closed.is_set() and (sip.isdeleted(owner) or not owner.isVisible()):
            self.close()
        self._closing_owner = None


__all__ = ["TerminologyPreviewCoordinator", "preview_identity", "profile_snapshot_key"]
