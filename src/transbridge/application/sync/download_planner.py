"""Download identity matching and explicit, snapshot-bound conflict decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace

from .download_review import (
    DownloadCandidate,
    DownloadDecision,
    DownloadDecisionStaleError,
    DownloadReviewRow,
)
from .models import (
    ConflictPolicy,
    DeletionPolicy,
    EntrySummary,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncAction,
    SyncOperation,
    SyncPlanItem,
    canonical_hash,
)
from .planner import _group, _local_payload, _plan_item, _remote_payload


def plan_download(
    local: tuple[LocalEntrySnapshot, ...],
    remote: tuple[RemoteEntrySnapshot, ...],
    *,
    policy: ConflictPolicy,
    deletion_policy: DeletionPolicy,
    local_state_only: bool,
    scope: str,
    decisions: tuple[DownloadDecision, ...],
) -> tuple[tuple[SyncPlanItem, ...], tuple[DownloadReviewRow, ...]]:
    local_groups = _group(local)
    local_bare_counts = Counter(_bare_key(entry) for entry in local)
    by_key, by_ref = defaultdict(list), defaultdict(list)
    for entry in remote:
        by_key[_bare_key(entry)].append(entry)
        if _ref_key(entry) is not None:
            by_ref[_ref_key(entry)].append(entry)
    owners = defaultdict(set)
    for entry in local:
        if _ref_key(entry) is not None:
            owners[_ref_key(entry)].add(entry.entry_key)
    choices = {decision.row_id: decision.choice_token for decision in decisions}
    if len(choices) != len(decisions):
        raise ValueError("download decisions contain duplicate rows")
    used_choices, represented = set(), set()
    items, reviews, proposals = {}, {}, {}
    for key, locals_for_key in sorted(local_groups.items()):
        current = locals_for_key[0]
        candidates = tuple(
            sorted(
                set(by_key[_bare_key(current)] + by_ref.get(_ref_key(current), [])),
                key=lambda entry: canonical_hash(_remote_payload(entry)),
            )
        )
        represented.update(candidates)
        selected, reason = _match(current, candidates, by_ref, local_bare_counts)
        if len(locals_for_key) > 1:
            selected, reason = None, "duplicate_local_key"
        row = _review(scope, locals_for_key, candidates, reason, by_ref, owners)
        choice = choices.get(row.row_id, "")
        if choice:
            used_choices.add(row.row_id)
        if choice == "keep_local":
            items[key] = _skip(current, policy, "download_kept_local")
            reviews[key] = replace(row, selected_choice=choice, resolved=True)
            continue
        if choice:
            candidate = next((candidate for candidate in row.candidates if candidate.token == choice), None)
            if candidate is None or not row.allow_remote_choice or not candidate.selectable:
                raise DownloadDecisionStaleError("The selected remote candidate is no longer available.")
            selected = candidate.entry
        if selected is not None:
            candidate = next(candidate for candidate in row.candidates if candidate.entry == selected)
            if not candidate.selectable:
                selected, reason = None, candidate.reason
        if selected is None and reason:
            items[key] = _conflict(current, policy, reason)
            reviews[key] = replace(row, reason=reason, selected_choice=choice)
            continue
        item = _plan_item(
            key,
            current,
            selected,
            SyncOperation.DOWNLOAD,
            ConflictPolicy.PREFER_REMOTE if choice else policy,
            deletion_policy,
            local_state_only=local_state_only,
        )
        if (
            choice
            and selected is not None
            and item.reason == "unchanged"
            and current.external_ref != selected.external_ref
        ):
            item = replace(
                item,
                action=SyncAction.UPDATE_LOCAL,
                before=EntrySummary.from_local(current),
                after=EntrySummary.from_remote(selected),
                reason="download_remote_selected",
            )
        items[key] = item
        if item.action is SyncAction.CONFLICT:
            reviews[key] = replace(row, reason=item.reason)
        elif choice:
            reviews[key] = replace(row, selected_choice=choice, resolved=True)
        if selected is not None and item.action in {SyncAction.CREATE_LOCAL, SyncAction.UPDATE_LOCAL}:
            proposals[key] = (selected, row, choice)

    if set(choices) != used_choices:
        raise DownloadDecisionStaleError("The local entry or remote candidates changed; review the current snapshot.")
    # A single remote identity may never be attached to two local identities.
    proposed_refs = Counter(_ref_key(value[0]) for value in proposals.values() if _ref_key(value[0]) is not None)
    for key, (selected, row, choice) in proposals.items():
        if proposed_refs[_ref_key(selected)] > 1:
            items[key] = _conflict(row.local, policy, "shared_remote_candidate")
            reviews[key] = replace(row, reason="shared_remote_candidate", selected_choice=choice)

    for key, entries in sorted(_group(remote).items()):
        outside = tuple(entry for entry in entries if entry not in represented)
        if not outside or key in items:
            continue
        entry = outside[0]
        if local_state_only:
            summary = EntrySummary.from_remote(entry)
            items[key] = SyncPlanItem(
                key, SyncAction.SKIP, summary, summary, entry.external_ref, "outside_local_source", policy
            )
        elif len(entries) > 1 or len(by_ref.get(_ref_key(entry), ())) > 1 or owners.get(_ref_key(entry)):
            items[key] = SyncPlanItem(
                key,
                SyncAction.SKIP,
                EntrySummary.from_remote(entry),
                EntrySummary.from_remote(entry),
                entry.external_ref,
                "remote_only_ambiguous",
                policy,
            )
        else:
            items[key] = _plan_item(key, None, entry, SyncOperation.DOWNLOAD, policy, deletion_policy)
    return tuple(items[key] for key in sorted(items)), tuple(reviews[key] for key in sorted(reviews))


def _match(current, candidates, by_ref, local_bare_counts):
    linked = by_ref.get(_ref_key(current), ())
    if len(linked) == 1:
        return linked[0], ""
    if len(linked) > 1:
        return None, "duplicate_remote_id"
    if not candidates:
        return None, ""
    if current.entry_key.original is None and local_bare_counts[_bare_key(current)] == 1 and len(candidates) == 1:
        return candidates[0], ""
    originals = tuple(entry for entry in candidates if entry.original == current.original)
    if len(originals) == 1:
        return originals[0], ""
    contextual = tuple(entry for entry in originals if entry.context == current.context)
    if len(contextual) == 1:
        return contextual[0], ""
    return None, "duplicate_remote_key" if originals else "original_mismatch"


def _review(scope, local_entries, candidates, reason, by_ref, owners):
    current = local_entries[0]
    projected = []
    for entry in candidates:
        ref = _ref_key(entry)
        blocked = ""
        if ref is not None and len(by_ref[ref]) > 1:
            blocked = "duplicate_remote_id"
        elif ref is not None and owners[ref] - {current.entry_key}:
            blocked = "remote_already_linked"
        projected.append(DownloadCandidate(canonical_hash(_remote_payload(entry)), entry, not blocked, blocked))
    row_id = canonical_hash({
        "scope": scope,
        "local": [_local_payload(entry) for entry in local_entries],
        "candidates": [_remote_payload(entry) for entry in candidates],
    })
    return DownloadReviewRow(
        row_id,
        current,
        tuple(projected),
        reason,
        local_occurrences=len(local_entries),
        allow_remote_choice=len(local_entries) == 1 and any(candidate.selectable for candidate in projected),
    )


def _bare_key(entry):
    return entry.entry_key.namespace, entry.entry_key.local_key


def _ref_key(entry):
    if entry is None or entry.external_ref is None or entry.external_ref.opaque_id is None:
        return None
    return entry.external_ref.index_key


def _skip(local, policy, reason):
    summary = EntrySummary.from_local(local)
    return SyncPlanItem(local.entry_key, SyncAction.SKIP, summary, summary, local.external_ref, reason, policy)


def _conflict(local, policy, reason):
    summary = EntrySummary.from_local(local)
    return SyncPlanItem(local.entry_key, SyncAction.CONFLICT, summary, summary, local.external_ref, reason, policy)
