"""Match upload targets by proven identity and preserve remote source fields."""

from collections import Counter, defaultdict
from dataclasses import replace

from .candidate_matching import JournalOriginalIndex
from .models import ConflictPolicy, EntrySummary, SyncAction, SyncPlanItem, canonical_hash
from .planner import _group, _local_payload, _remote_payload
from .upload_review import UploadCandidate, UploadDecisionStaleError, UploadReviewRow


def plan_upload(local, remote, *, policy, scope, decisions, path_scoped=False):
    journal = JournalOriginalIndex(remote)
    by_key, by_ref, owners = defaultdict(list), defaultdict(list), defaultdict(set)
    for entry in remote:
        by_key[_bare(entry)].append(entry)
        if _ref(entry, scope):
            by_ref[_ref(entry, scope)].append(entry)
    for entry in local:
        if _ref(entry, scope):
            owners[_ref(entry, scope)].add(entry.entry_key)
    bare_counts = Counter(_bare(entry) for entry in local)
    choices = {decision.row_id: decision.choice_token for decision in decisions}
    if len(choices) != len(decisions):
        raise ValueError("upload decisions contain duplicate rows")
    items, reviews, proposals = {}, {}, {}
    represented, used = set(), set()
    for key, locals_for_key in sorted(_group(local).items()):
        current = locals_for_key[0]
        candidates = tuple(
            sorted(
                set(by_key[_bare(current)] + by_ref.get(_ref(current, scope), []))
                | set(journal.alternatives(current, by_key[_bare(current)])),
                key=lambda entry: canonical_hash(_remote_payload(entry)),
            )
        )
        represented.update(candidates)
        selected, reason = _match(current, candidates, by_ref, bare_counts, scope, path_scoped=path_scoped)
        if len(locals_for_key) > 1:
            selected, reason = None, "duplicate_local_key"
        row = _review(scope, locals_for_key, candidates, reason, by_ref, owners)
        choice = choices.get(row.row_id, "")
        if choice:
            used.add(row.row_id)
        if choice == "keep_remote":
            items[key] = _item(current, None, SyncAction.SKIP, policy, "upload_kept_remote")
            reviews[key] = replace(row, selected_choice=choice, resolved=True)
            continue
        if choice:
            candidate = next((candidate for candidate in row.candidates if candidate.token == choice), None)
            if candidate is None or not row.allow_remote_choice or not candidate.selectable:
                raise UploadDecisionStaleError("The selected upload target is no longer available.")
            selected, reason = candidate.entry, ""
        if selected is not None:
            candidate = next(candidate for candidate in row.candidates if candidate.entry == selected)
            if not candidate.selectable:
                selected, reason = None, candidate.reason
        if reason:
            item = _item(current, None, SyncAction.CONFLICT, policy, reason)
        else:
            item = _plan_target(current, selected, ConflictPolicy.PREFER_LOCAL if choice else policy)
        items[key] = item
        if item.action is SyncAction.CONFLICT:
            reviews[key] = replace(row, reason=item.reason, selected_choice=choice)
        elif choice:
            reviews[key] = replace(row, selected_choice=choice, resolved=True)
        if selected is not None and item.action in {SyncAction.UPDATE_REMOTE, SyncAction.DELETE_REMOTE}:
            proposals[key] = selected, row, choice
    if set(choices) != used:
        raise UploadDecisionStaleError("The local entry or upload candidates changed; review the current snapshot.")
    targets = Counter(_ref(value[0], scope) for value in proposals.values())
    for key, (selected, row, choice) in proposals.items():
        if targets[_ref(selected, scope)] > 1:
            items[key] = _item(row.local, None, SyncAction.CONFLICT, policy, "shared_remote_candidate")
            reviews[key] = replace(row, reason="shared_remote_candidate", selected_choice=choice)
    for key, entries in sorted(_group(remote).items()):
        outside = tuple(entry for entry in entries if entry not in represented)
        if not outside or key in items:
            continue
        entry = outside[0]
        summary = EntrySummary.from_remote(entry)
        items[key] = SyncPlanItem(key, SyncAction.SKIP, summary, summary, entry.external_ref, "remote_only", policy)
    return tuple(items[key] for key in sorted(items)), tuple(reviews[key] for key in sorted(reviews))


def _match(local, candidates, by_ref, bare_counts, scope, *, path_scoped):
    reference = _ref(local, scope)
    if reference:
        linked = by_ref.get(reference, ())
        if len(linked) == 1:
            return linked[0], ""
        if linked or not path_scoped:
            return None, "duplicate_remote_id" if linked else "stale_remote_reference"
    if not candidates:
        if bare_counts[_bare(local)] > 1 or (local.entry_key.original is not None and not path_scoped):
            return None, "original_match_required"
        return None, ""
    originals = tuple(entry for entry in candidates if entry.original == local.original)
    if len(originals) == 1:
        return originals[0], ""
    contextual = tuple(entry for entry in originals if entry.context == local.context)
    if len(contextual) == 1:
        return contextual[0], ""
    return None, "duplicate_remote_key" if originals else "original_mismatch"


def _review(scope, locals_for_key, candidates, reason, by_ref, owners):
    current = locals_for_key[0]
    projected = []
    for entry in candidates:
        reference = _ref(entry, scope)
        blocked = ""
        if reference is None:
            blocked = "remote_id_missing"
        elif len(by_ref[reference]) > 1:
            blocked = "duplicate_remote_id"
        elif owners[reference] - {current.entry_key}:
            blocked = "remote_already_linked"
        elif entry.deleted:
            blocked = "remote_deleted"
        projected.append(UploadCandidate(canonical_hash(_remote_payload(entry)), entry, not blocked, blocked))
    row_id = canonical_hash({
        "operation": "upload",
        "scope": scope,
        "local": [_local_payload(entry) for entry in locals_for_key],
        "candidates": [_remote_payload(entry) for entry in candidates],
    })
    return UploadReviewRow(
        row_id,
        current,
        tuple(projected),
        reason,
        local_occurrences=len(locals_for_key),
        allow_remote_choice=len(locals_for_key) == 1 and any(candidate.selectable for candidate in projected),
    )


def _plan_target(local, remote, policy):
    if remote is None:
        action = SyncAction.SKIP if local.deleted else SyncAction.CREATE_REMOTE
        return _item(local, None, action, policy, "local_tombstone_only" if local.deleted else "local_only")
    if local.deleted:
        return _item(local, remote, SyncAction.DELETE_REMOTE, policy, "local_tombstone")
    same = (local.translation, local.stage) == (remote.translation, remote.stage)
    if same and local.external_ref == remote.external_ref:
        return _item(local, remote, SyncAction.SKIP, policy, "unchanged")
    if policy in {ConflictPolicy.PREFER_REMOTE, ConflictPolicy.SKIP}:
        return _item(local, remote, SyncAction.SKIP, policy, "remote_preferred")
    if not same and policy is ConflictPolicy.ABORT:
        return _item(local, remote, SyncAction.CONFLICT, policy, "content_changed")
    return _item(
        local,
        remote,
        SyncAction.UPDATE_REMOTE,
        policy,
        "upload_association_confirmed" if same else "local_preferred",
    )


def _item(local, remote, action, policy, reason):
    return SyncPlanItem(
        local.entry_key,
        action,
        None if remote is None else EntrySummary.from_remote(remote),
        EntrySummary.from_local(local),
        None if remote is None else remote.external_ref,
        reason,
        policy,
    )


def _bare(entry):
    return entry.entry_key.namespace, entry.entry_key.local_key


def _ref(entry, scope):
    reference = entry.external_ref
    if reference is None or reference.system != "paratranz":
        return None
    if isinstance(reference.opaque_id, bool) or not isinstance(reference.opaque_id, int) or reference.opaque_id < 1:
        return None
    if scope.startswith("paratranz:project:"):
        expected = scope.removeprefix("paratranz:").split(":source:", 1)[0]
        if reference.scope != expected:
            return None
    return reference.index_key
