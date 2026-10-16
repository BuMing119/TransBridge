"""Immutable, Qt-free synchronization display indexes with bounded row formatting."""

from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType

from .sync_copy import (
    ACTION_LABELS,
    EFFECT_LABELS,
    REASON_LABELS,
    STATUS_LABELS,
    content_text,
    import_diagnostic,
    label,
    plan_comparison,
)


@dataclass(frozen=True, slots=True)
class SyncDetailRow:
    item_id: str
    cells: tuple[str, ...]


class OutcomeProjection:
    def __init__(self, result, items=()):
        self.source, self.items = result, items
        self.total = len(result.outcomes)
        self._names = MappingProxyType({
            item.item_id: content.original for item in items if (content := item.local or item.remote) is not None
        })
        counts = Counter(str(item.status) for item in result.outcomes)
        self.counts = MappingProxyType(dict(counts))
        self.detail = "、".join(f"{label(name, STATUS_LABELS)} {count} 项" for name, count in counts.items())
        self.partial = bool(set(counts) & {"failed", "unknown", "cancelled"})

    def page(self, offset, limit):
        return tuple(self._row(item) for item in self.source.outcomes[offset : offset + limit])

    def _row(self, item):
        name = self._names.get(item.item_id, item.item_id)
        if item.remote_id is not None:
            name += f" · 远端编号 {item.remote_id}"
        return SyncDetailRow(
            item.item_id,
            (
                name,
                label(item.action, ACTION_LABELS),
                label(item.status, STATUS_LABELS),
                f"{item.message}\n（{item.code}）",
            ),
        )


class ProposalProjection:
    def __init__(self, proposal):
        self.source = proposal
        self.total = len(getattr(proposal, "mutations", ()))
        selection = getattr(proposal, "selection", None)
        self.choices = MappingProxyType({item.item_id: item for item in getattr(selection, "choices", ())})
        self.committable = proposal.committable
        self.first_blocked = next(
            (item.item_id for item in getattr(proposal, "mutations", ()) if str(item.effect) == "conflict"), None
        )
        self.diagnostics = "\n".join(
            import_diagnostic(item.split(":", 1)[-1]) for item in getattr(proposal, "diagnostics", ())
        )
        self.counts = "、".join(
            f"{label(name, EFFECT_LABELS)} {count} 项" for name, count in getattr(proposal, "counts", ())
        )

    def page(self, offset, limit):
        return tuple(_proposal_row(item) for item in getattr(self.source, "mutations", ())[offset : offset + limit])


def _proposal_row(item):
    return SyncDetailRow(
        item.item_id,
        (
            label(item.effect, EFFECT_LABELS),
            content_text(item.before),
            content_text(item.after),
            import_diagnostic(item.diagnostic) or "可以提交到草稿；发布后生效",
        ),
    )


def plan_rows(items):
    return tuple(
        SyncDetailRow(
            item.item_id, (label(item.action, ACTION_LABELS), *plan_comparison(item), label(item.reason, REASON_LABELS))
        )
        for item in items
    )


def proposal_rows(proposal):
    """Compatibility helper for consumers explicitly requesting all rows."""
    return tuple(_proposal_row(item) for item in getattr(proposal, "mutations", ()))


def outcome_rows(result, items=()):
    projection = OutcomeProjection(result, items)
    return projection.page(0, projection.total)
