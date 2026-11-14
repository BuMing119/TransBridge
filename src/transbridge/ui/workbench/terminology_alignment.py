# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Optional model work over captured preview inputs, isolated from Qt state."""

import logging

from PyQt6.QtCore import QObject, QRunnable, QTimer

from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput
from transbridge.ui.project_rows import ProjectRowsCollection

logger = logging.getLogger(__name__)


class AlignmentConsentRequired(RuntimeError):
    """A real cache miss needs the launch's choice before any model preparation."""


def freeze_alignment_scope(ctx):
    """Capture every source, retaining lazy revision-bound reads outside the GUI thread."""
    resident, deferred = [], []
    for slot in ctx.slots.values():
        collection = slot.collection
        if isinstance(collection, ProjectRowsCollection):
            # query_rows fixes the token and immutable edit-overlay reference now.
            # Its database iterator is consumed only by the alignment worker.
            deferred.append((entry for entry, _key, _labels in collection.query_rows()))
        else:
            resident.extend(collection)
    return tuple(resident), tuple(deferred)


class PreviewAlignmentCancellation(QueryCancellation):
    def __init__(self, cancelled, closed):
        super().__init__()
        self._preview_cancelled, self._owner_closed = cancelled, closed

    @property
    def is_cancelled(self):
        return super().is_cancelled or self._preview_cancelled.is_set() or self._owner_closed.is_set()


class LazyAlignmentService:
    """Open model resources only if the rule pass actually requests decisions."""

    def __init__(self, prepare=None, *, require_consent=False):
        self._prepare = prepare
        self._require_consent = require_consent

    def resolve_many(self, requests, *, cancellation=None, progress=None):
        if not requests:
            return ()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if self._require_consent:
            raise AlignmentConsentRequired("规则与缓存无法确定部分术语位置，等待选择是否启用 AI。")
        from transbridge.bootstrap.terminology_alignment import prepare_alignment

        if cancellation is not None:
            cancellation.raise_if_cancelled()
        prepared = (self._prepare or prepare_alignment)()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        with prepared.open() as service:
            return service.resolve_many(requests, cancellation=cancellation, progress=progress)


class AlignmentPreviewCall(QRunnable):
    def __init__(
        self,
        generation,
        profile,
        base,
        chunks,
        source,
        prior,
        cancellation,
        signals,
        prepare=None,
        deferred=(),
        retry_uncertain=True,
        cache=None,
        allow_model=True,
        require_consent=False,
    ):
        super().__init__()
        self._generation, self._profile, self._base = generation, profile, base
        self._entries = tuple(entry for chunk in chunks for entry in chunk)
        self._source, self._prior, self._cancellation = source, prior, cancellation
        self._signals, self._prepare = signals, prepare
        self._deferred = deferred
        self._retry_uncertain = retry_uncertain
        self._cache = cache
        self._allow_model = allow_model
        self._require_consent = require_consent

    def run(self):
        result, error = None, None
        try:
            self._cancellation.raise_if_cancelled()
            from transbridge.application.terminology_profiles.profile_alignment import align_profile_entries

            entries = list(self._entries)
            for source in self._deferred:
                for entry in source:
                    self._cancellation.raise_if_cancelled()
                    entries.append(ProfileEntryInput.capture(entry))
            self._cancellation.raise_if_cancelled()
            result = align_profile_entries(
                tuple(entries),
                self._profile,
                self._base,
                source_profile=self._source,
                service=LazyAlignmentService(self._prepare, require_consent=self._require_consent),
                cancellation=self._cancellation,
                progress=self._progress,
                prior=self._prior,
                retry_uncertain=self._retry_uncertain,
                **({"cache": self._cache, "allow_model": self._allow_model} if self._cache is not None else {}),
            )
            self._cancellation.raise_if_cancelled()
        except QueryCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - deliver provider and preparation failures to the owning preview
            error = exc
        if not self._cancellation.is_cancelled:
            self._signals.aligned.emit(self._generation, result, error)

    def _progress(self, completed, total):
        if not self._cancellation.is_cancelled:
            self._signals.progress.emit(self._generation, completed, total)


class AlignmentBarBinding(QObject):
    """Inspect stable inputs automatically and request consent only on model misses."""

    def __init__(self, bar, preview, *, scope=None, context_identity=None, consent=None, delay_ms=300):
        super().__init__(bar)
        from .alignment_consent import get_alignment_consent

        self._bar, self._preview = bar, preview
        self._state = None
        self._scope = scope
        self._context_identity = context_identity or (lambda: None)
        self._consent = consent if consent is not None else get_alignment_consent()
        self._attempted = []
        self._scheduled_key = None
        self._notice = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._automatic)
        self._consent.changed.connect(self.render)
        bar.alignment_requested.connect(self._activate)
        if preview is not None:
            preview.changed.connect(self.render)
            preview.alignment_status_changed.connect(self.render)
            preview.alignment_consent_required.connect(self._request_consent)

    def _request_consent(self):
        if self._ready() and not self._consent.decided:
            self._consent.request(self._bar.window())

    def set_state(self, state):
        self._state = state
        self.render()

    def _activate(self):
        if self._preview is None:
            return
        if self._preview.alignment_running:
            self._preview.cancel_alignment()
        elif self._consent.enabled and self._ready() and self._key() in self._attempted:
            self._launch(retry=True)

    def _ready(self):
        return bool(
            (
                not self._consent.decided
                or self._consent.enabled
                or (self._preview is not None and self._preview.cache_available)
            )
            and self._state is not None
            and self._state.enabled
            and self._state.is_preview
            and self._preview is not None
            and not self._preview.pending
            and not self._preview.error
        )

    def _key(self):
        from .terminology_preview import profile_snapshot_key

        return (
            self._context_identity(),
            profile_snapshot_key(
                self._state.selected_revision, self._state.base_snapshot, self._state.applied_revision
            ),
            self._consent.enabled,
            self._consent.decided,
        )

    def _automatic(self):
        if self._ready() and self._key() not in self._attempted:
            self._launch(retry=False)

    def _launch(self, *, retry):
        key = self._key()
        if key not in self._attempted:
            self._attempted.append(key)
        self._notice = ""
        try:
            resident, deferred = (None, ()) if self._scope is None else self._scope()
            started = self._preview.start_alignment(
                entries=resident,
                deferred=deferred,
                retry_uncertain=retry,
                allow_model=self._consent.enabled or not self._consent.decided,
                require_consent=not self._consent.decided,
            )
            if not started:
                self._attempted.remove(key)
                return  # Await a real input/state change; do not poll an empty scope.
        except Exception as exc:  # noqa: BLE001 - report capture failure once; never loop automatic requests
            logger.warning("Cannot capture automatic terminology alignment input", exc_info=True)
            self._notice = f"自动定位未开始：{exc}。可重新定位。"
        self.render()

    def render(self):
        state, preview = self._state, self._preview
        running = preview is not None and preview.alignment_running
        if running and preview.alignment_uses_model and self._consent.decided and not self._consent.enabled:
            preview.cancel_alignment()
            return
        ready = self._ready()
        attempted = state is not None and self._key() in self._attempted
        self._bar.alignment_button.setText("取消定位" if running else "重新定位")
        retry_available = ready and attempted and self._consent.enabled
        self._bar.alignment_button.setVisible(running or retry_available)
        self._bar.alignment_button.setEnabled(running or retry_available)
        if ready and not attempted:
            key = self._key()
            if not self._timer.isActive() or self._scheduled_key != key:
                self._scheduled_key = key
                self._timer.start()
        else:
            self._scheduled_key = None
            self._timer.stop()
        message = "" if preview is None else preview.alignment_message
        detail = self._notice or message or ("" if state is None else state.detail)
        self._bar.detail.set_full_text(detail)
        self._bar.detail.setToolTip(detail)
        self._bar.detail.setAccessibleDescription(detail)
