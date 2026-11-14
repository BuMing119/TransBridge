# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind application browse queries to independent effective and draft tables."""

from PyQt6.QtCore import QEvent, QObject, QTimer

from transbridge.application.terminology.browse_queries import TermBrowseQuery, TermFilter


class TermBrowsingController:
    def __init__(self, context, effective_model, draft_model):
        self.context = context
        self.models = {"effective": effective_model, "draft": draft_model}
        self.filters = {name: TermFilter() for name in self.models}
        self._bound = set()
        self._views = {}
        self._dirty = set()
        self._activation = []

    def bind_views(self, **views):
        self._views = views
        self._activation = [
            _PageActivation(view, lambda name=name: self.activate(name)) for name, view in views.items()
        ]

    def activate(self, section):
        if section in self._dirty or section not in self._bound:
            self._bind(section)

    def replace_filter(self, section, filters):
        self.filters[section] = filters
        self._bind(section)

    def refresh(self):
        if self.context.project_id is None or self.context.variant_id is None:
            for model in self.models.values():
                model.clear()
            return
        for name, model in self.models.items():
            if name in self._views and not self._views[name].isVisible():
                model.clear()
                self._bound.discard(name)
                self._dirty.add(name)
                continue
            if name in self._bound:
                model.refresh()
            else:
                self._bind(name)

    def refresh_effective(self):
        """Invalidate in-flight pages before reading assets for a new profile."""
        self.models["effective"].clear()
        self._bound.discard("effective")
        self._bind("effective")

    def _bind(self, section):
        if self.context.project_id is None or self.context.variant_id is None:
            return
        if section in self._views and not self._views[section].isVisible():
            self._dirty.add(section)
            return
        query = TermBrowseQuery(self.context.project_id, self.context.variant_id, self.filters[section])
        self.models[section].set_query(query, query_fingerprint=repr(query.filters))
        self._bound.add(section)
        self._dirty.discard(section)


class _PageActivation(QObject):
    def __init__(self, view, activate):
        super().__init__(view)
        self._activate = activate
        view.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self._activate)
        return super().eventFilter(watched, event)
