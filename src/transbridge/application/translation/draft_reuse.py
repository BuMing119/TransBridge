"""Durable early draft commits and evidence for continuing the same translation task."""

from __future__ import annotations

from dataclasses import dataclass, replace

from transbridge.application.contracts import OperationOutcome, OperationResult, RequestContext
from transbridge.application.io.identity import EntryKey
from transbridge.application.io.mutation import CollectionMutationPort, EntrySnapshot
from transbridge.application.io.publish import PublishCommitGuard

from .candidate_checkpoint import TranslationCheckpointPort
from .legacy_bridge import LegacyTranslationCandidateSession
from .workload_models import TranslationInput, canonical_hash, translation_input_fingerprint


@dataclass(frozen=True, slots=True)
class CommittedDraftTranslations:
    """Before inputs and exact after snapshots, backed by a completed guarded commit."""

    inputs: tuple[TranslationInput, ...] = ()
    snapshots: tuple[EntrySnapshot, ...] = ()
    commit_id: str = ""

    def __post_init__(self):
        if tuple(item.entry_key for item in self.inputs) != tuple(item.entry_key for item in self.snapshots):
            raise ValueError("草稿复用输入与提交证据的词条身份不一致")
        if self.inputs and not self.commit_id:
            raise ValueError("草稿复用缺少提交证据")
        for before, after in zip(self.inputs, self.snapshots, strict=True):
            if (
                after.original != before.original
                or (after.context or "") != before.context
                or after.revision != before.revision.next()
                or not after.translation.strip()
                or after.stage != 2
                or not any(
                    item.source == "translation-workload-v2" and dict(item.metadata).get("commit_id") == self.commit_id
                    for item in after.provenance
                )
            ):
                raise ValueError("草稿复用快照不具备有效提交证据")

    @property
    def keys(self) -> frozenset[EntryKey]:
        return frozenset(item.entry_key for item in self.inputs)

    def require_current(self, collection: CollectionMutationPort, target_keys=None) -> None:
        if target_keys is not None and not self.keys.issubset(target_keys):
            raise ValueError("草稿复用包含不属于当前翻译范围的词条")
        for snapshot in self.snapshots:
            if collection.snapshot(snapshot.entry_key) != snapshot:
                raise ValueError(f"提前复用的草稿已变化，不能接续：{snapshot.entry_key.local_key}")

    def spec(self) -> dict:
        if not self.inputs:
            return {}
        return {
            "committed_draft_reuse": canonical_hash({
                "commit_id": self.commit_id,
                "inputs": [item.to_dict() for item in self.inputs],
                "after": [
                    {"key": item.entry_key.to_dict(), "revision": item.revision.value, "text": item.translation}
                    for item in self.snapshots
                ],
            })
        }

    def include_in_commit(self, result: OperationResult[dict], collection: CollectionMutationPort):
        """Combine completed evidence, without applying the early translations again."""
        if not self.inputs:
            return result
        self.require_current(collection)
        value = dict(result.value or {})
        value["already_committed_keys"] = [
            *value.get("already_committed_keys", ()),
            *(item.entry_key.to_dict() for item in self.inputs),
        ]
        value["draft_reuse_commit_id"] = self.commit_id
        return replace(
            result,
            outcome=(
                OperationOutcome.COMPLETED if result.outcome is OperationOutcome.COMPLETED else OperationOutcome.PARTIAL
            ),
            value=value,
            counts=replace(result.counts, skipped=result.counts.skipped + len(self.inputs)),
        )


def commit_draft_reuse(
    inputs: tuple[TranslationInput, ...],
    translations: dict[str, str],
    *,
    collection,
    run_id: str,
    checkpoint: TranslationCheckpointPort,
    guard: PublishCommitGuard,
) -> CommittedDraftTranslations:
    """Save candidates before the guarded draft write; refuse incomplete commit evidence."""
    if not inputs:
        return CommittedDraftTranslations()
    for item in inputs:
        current = collection.snapshot(item.entry_key)
        if current is None or (
            current.revision,
            current.original,
            current.translation,
            current.stage,
            current.context or "",
        ) != (item.revision, item.original, item.translation, item.stage, item.context):
            raise ValueError(f"名称复用前草稿输入已变化：{item.entry_key.local_key}")
    session = LegacyTranslationCandidateSession(
        run_id=run_id,
        owner_id="project-name-reuse",
        spec_fingerprint=canonical_hash({"kind": "project-name-reuse-v1", "translations": translations}),
        input_fingerprint=translation_input_fingerprint(inputs),
        checkpoint=checkpoint,
        provider="local-terminology",
        model="",
    )
    session.accept(translations, collection)
    result = session.commit(
        collection,
        RequestContext(
            "project-name-reuse",
            run_id=run_id,
            permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
        ),
        guard,
    )
    committed = {
        EntryKey.from_dict(key)
        for field in ("applied_keys", "already_committed_keys")
        for key in (result.value or {}).get(field, ())
    }
    if result.outcome is not OperationOutcome.COMPLETED or committed != {item.entry_key for item in inputs}:
        details = "; ".join(f"{item.code}: {item.message}" for item in result.diagnostics)
        raise RuntimeError(f"已有名称译文未能完整提交到任务草稿：{details or result.outcome}")
    return CommittedDraftTranslations(
        inputs,
        tuple(collection.snapshot(item.entry_key) for item in inputs),
        result.value["commit_id"],
    )
