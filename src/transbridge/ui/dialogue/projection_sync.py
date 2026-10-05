"""Avoid rebuilding unchanged authoritative entry projections during navigation."""

from __future__ import annotations

import weakref

from transbridge.ui.source_hydration import apply_variant_projection

from .editing import content_scope


class EditorProjectionSync:
    def __init__(self, projection) -> None:
        self._projection = projection
        self._context = None
        self._listener = None
        self._snapshot = self._collection = None
        self._token = None
        self._notifications = 0

    def _observe(self, context) -> None:
        if self._context is context:
            return
        if self._listener is not None:
            self._context.collection_changed.disconnect(self._listener)
        self._context = context
        self._token = None
        signal = getattr(context, "collection_changed", None)
        if signal is not None:
            owner = weakref.ref(self)

            def invalidate(*_args):
                if (sync := owner()) is not None:
                    sync._token = None
                    sync._notifications += 1

            self._listener = invalidate
            signal.connect(invalidate)
        else:
            self._listener = None

    @staticmethod
    def _state_token(context, snapshot, collection):
        return (
            content_scope(context),
            getattr(snapshot, "stream_id", None),
            getattr(snapshot, "revision", None),
            collection.collection_revision,
        )

    def _remember(self, context, snapshot, collection, token, notifications) -> None:
        if (
            context.collection is collection
            and self._state_token(context, snapshot, collection) == token
            and self._projection is not None
            and self._projection.snapshot() is snapshot
            and self._notifications == notifications
        ):
            self._snapshot, self._collection, self._token = snapshot, collection, token
        else:
            self._token = None

    def publish_applied(self, context, snapshot) -> None:
        """Publish a collection already projected by the commit, then retain its proof.

        Exactly one notification is our own. Nested notifications or replacement
        state invalidate this proof, including legacy writes without revisions.
        """
        self._observe(context)
        collection = context.collection
        token = self._state_token(context, snapshot, collection)
        notifications = self._notifications + 1
        context.collection_changed.emit(collection)
        self._remember(context, snapshot, collection, token, notifications)

    def sync(self, context) -> bool:
        self._observe(context)
        collection = context.collection
        snapshot = None if self._projection is None else self._projection.snapshot()
        if collection is None or snapshot is None:
            self._token = None
            self._snapshot = self._collection = None
            return False
        revision = getattr(snapshot, "revision", None)
        token = self._state_token(context, snapshot, collection)
        if (
            revision is not None
            and snapshot is self._snapshot
            and collection is self._collection
            and token == self._token
        ):
            return False
        states = snapshot.to_dict()["values"].get("entries", ())
        projected = apply_variant_projection(collection, states)
        changed = tuple(projected) != tuple(collection)
        expected = projected if changed else collection
        expected_token = (*token[:3], expected.collection_revision)
        notifications = self._notifications
        if changed:
            context.collection = projected
            # AppContext emits once; data-only callers may have no notifying setter.
            notifications += int(self._notifications != notifications)
        # Setting the collection emits synchronously. Cache the original scope
        # only if observers did not replace the collection or switch content.
        self._remember(context, snapshot, expected, expected_token, notifications)
        return changed
