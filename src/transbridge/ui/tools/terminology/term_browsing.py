"""Bind application browse queries to independent effective and draft tables."""

from transbridge.application.terminology.browse_queries import TermBrowseQuery, TermFilter


class TermBrowsingController:
    def __init__(self, context, effective_model, draft_model):
        self.context = context
        self.models = {"effective": effective_model, "draft": draft_model}
        self.filters = {name: TermFilter() for name in self.models}
        self._bound = set()

    def replace_filter(self, section, filters):
        self.filters[section] = filters
        self._bind(section)

    def refresh(self):
        if self.context.project_id is None or self.context.variant_id is None:
            for model in self.models.values():
                model.clear()
            return
        for name, model in self.models.items():
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
        query = TermBrowseQuery(self.context.project_id, self.context.variant_id, self.filters[section])
        self.models[section].set_query(query, query_fingerprint=repr(query.filters))
        self._bound.add(section)
