"""Download identity matching and explicit, snapshot-bound conflict decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace

from transbridge.application.io.identity import EntryKey

from .candidate_matching import (
    JournalOriginalIndex,
    OriginalContentIndex,
    consistent_original_identity,
    equivalent_download_source,
    group_download_candidates,
    group_translation_candidates,
)
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
    preserve_local_source: bool = False,
    review_keys: tuple[EntryKey, ...] = (),
    content_download: bool = False,
) -> tuple[tuple[SyncPlanItem, ...], tuple[DownloadReviewRow, ...]]:
    local_groups = _group(local)
    journal = JournalOriginalIndex(remote)
    content_index = (
        OriginalContentIndex(remote, order_key=lambda entry: canonical_hash(_remote_payload(entry)))
        if content_download
        else None
    )
    by_key, by_ref = defaultdict(list), defaultdict(list)
    for entry in remote:
        by_key[_bare_key(entry)].append(entry)
        if _ref_key(entry) is not None:
            by_ref[_ref_key(entry)].append(entry)
    choices = {decision.row_id: decision.choice_token for decision in decisions}
    if len(choices) != len(decisions):
        raise ValueError("download decisions contain duplicate rows")
    used_choices, represented = set(), set()
    represented_groups = set()
    items, reviews, proposals = {}, {}, {}
    forced_review = frozenset(review_keys)
    for key, locals_for_key in sorted(local_groups.items()):
        current = locals_for_key[0]
        candidates = (
            content_index.candidates(current, by_key[_bare_key(current)])
            if content_index is not None
            else tuple(
                sorted(
                    set(by_key[_bare_key(current)]) | set(journal.alternatives(current, by_key[_bare_key(current)])),
                    key=lambda entry: canonical_hash(_remote_payload(entry)),
                )
            )
        )
        if content_index is None or id(candidates) not in represented_groups:
            represented.update(candidates)
            represented_groups.add(id(candidates))
        selected, reason, equivalent = (
            content_index.match(current) if content_index is not None else _match(current, candidates, by_ref)
        )
        if len(locals_for_key) > 1:
            selected, reason = None, "duplicate_local_key"
        needs_review = (
            not content_download
            or key in forced_review
            or policy is ConflictPolicy.ABORT
            or selected is None
            and reason not in {"no_matching_original", "remote_untranslated"}
        )
        row = (
            _review(
                scope,
                locals_for_key,
                candidates,
                "local_content_changed" if key in forced_review else reason,
                by_ref,
                content_download=content_download,
            )
            if needs_review
            else None
        )
        choice = choices.get(row.row_id, "") if row is not None else ""
        if choice:
            used_choices.add(row.row_id)
        if choice == "keep_local":
            items[key] = _skip(current, policy, "download_kept_local")
            reviews[key] = replace(row, selected_choice=choice, resolved=True)
            continue
        if key in forced_review and not choice:
            items[key] = _conflict(current, policy, "local_content_changed")
            reviews[key] = row
            continue
        if choice:
            candidate = next((candidate for candidate in row.candidates if candidate.token == choice), None)
            if candidate is None or not row.allow_remote_choice or not candidate.selectable:
                raise DownloadDecisionStaleError("The selected remote candidate is no longer available.")
            selected, equivalent = candidate.entry, bool(candidate.equivalent_entries)
        if selected is not None and not equivalent:
            candidate = next(candidate for candidate in row.candidates if candidate.entry == selected)
            if not candidate.selectable:
                selected, reason = None, candidate.reason
        if selected is None and reason:
            if content_download and reason in {"no_matching_original", "remote_untranslated"}:
                items[key] = _skip(current, policy, reason)
            else:
                items[key] = _conflict(current, policy, reason)
                reviews[key] = replace(row, reason=reason, selected_choice=choice)
            continue
        item = _plan_item(
            key,
            # The shared planner compares references for bidirectional sync.
            # Downloads compare content; the selected ID only identifies the
            # reviewed source in this snapshot and is never a local binding.
            replace(current, external_ref=selected.external_ref) if selected is not None else current,
            selected,
            SyncOperation.DOWNLOAD,
            ConflictPolicy.PREFER_REMOTE if choice else policy,
            deletion_policy,
            local_state_only=local_state_only,
            preserve_local_source=preserve_local_source or content_download,
        )
        if equivalent and item.action is SyncAction.UPDATE_LOCAL:
            item = replace(item, reason=reason if content_download and not choice else "equivalent_remote_content")
        items[key] = item
        if item.action is SyncAction.CONFLICT:
            reviews[key] = replace(row, reason=item.reason)
        elif choice:
            reviews[key] = replace(row, selected_choice=choice, resolved=True)
        if _ref_key(selected) is not None and item.action in {SyncAction.CREATE_LOCAL, SyncAction.UPDATE_LOCAL}:
            proposals[key] = (selected, row, choice)

    if set(choices) != used_choices:
        raise DownloadDecisionStaleError("The local entry or remote candidates changed; review the current snapshot.")
    # One source record cannot silently satisfy two distinct local identities.
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
        elif len(entries) > 1 or len(by_ref.get(_ref_key(entry), ())) > 1:
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


def _match(current, candidates, by_ref):
    if not consistent_original_identity(current):
        return None, "original_mismatch", False
    if not candidates:
        return None, "", False
    originals = tuple(
        entry for entry in candidates if entry.original == current.original and consistent_original_identity(entry)
    )
    if len(originals) == 1:
        return originals[0], "", False
    contextual = tuple(entry for entry in originals if entry.context == current.context)
    if len(contextual) == 1:
        return contextual[0], "", False
    equivalent = equivalent_download_source(contextual or originals, current.entry_key)
    if equivalent is not None:
        if any(len(by_ref.get(_ref_key(entry), ())) > 1 for entry in contextual or originals):
            return None, "duplicate_remote_id", False
        return equivalent, "", True
    return None, "duplicate_remote_key" if originals else "original_mismatch", False


def _review(scope, local_entries, candidates, reason, by_ref, *, content_download=False):
    current = local_entries[0]
    projected = []
    for entry in candidates:
        ref = _ref_key(entry)
        blocked = ""
        if not content_download and ref is not None and len(by_ref[ref]) > 1:
            blocked = "duplicate_remote_id"
        elif not consistent_original_identity(current) or not consistent_original_identity(entry):
            blocked = "original_mismatch"
        elif content_download and entry.original != current.original:
            blocked = "original_mismatch"
        elif content_download and (entry.deleted or not entry.translation):
            blocked = "remote_untranslated"
        projected.append(DownloadCandidate(canonical_hash(_remote_payload(entry)), entry, not blocked, blocked))
    row_id = canonical_hash({
        "scope": scope,
        "local": [_local_payload(entry) for entry in local_entries],
        "candidates": [_remote_payload(entry) for entry in candidates],
    })
    return DownloadReviewRow(
        row_id,
        current,
        group_translation_candidates(tuple(projected), current)
        if content_download
        else group_download_candidates(tuple(projected), current),
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
