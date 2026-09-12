"""Context checkpoints and append segments in the existing immutable artifact store."""

from dataclasses import dataclass, replace
import json

from transbridge.application.assistant_context.models import ContextEpoch, PreparationWait, encode, validate_successor
from transbridge.application.assistant_requests.transcript import AttachmentRef
from transbridge.persistence.v2.models import BackupVerificationError, PathBoundaryError


@dataclass(frozen=True)
class StoredContext:
    epoch: ContextEpoch
    head: dict
    references: tuple[AttachmentRef, ...]
    summary_refs: tuple[AttachmentRef, ...]


class AssistantContextStore:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def read(self, session_id, request_id, scope, head) -> StoredContext | None:
        if head is None:
            return None
        if type(head.get("schema_version")) is not int or head["schema_version"] != 1:
            raise PreparationWait("CONTEXT_VERSION_UNSUPPORTED", "上下文版本不受支持。")
        try:
            reference = AttachmentRef.from_dict(head["artifact"])
            documents, references, visited = [], [], set()
            while True:
                if reference.path in visited:
                    raise ValueError("Context artifact cycle")
                visited.add(reference.path)
                references.append(reference)
                document = json.loads(self.artifacts.read_artifact(session_id, reference))
                if (
                    document.get("schema_version") != 1
                    or document.get("kind") != "assistant_context"
                    or document.get("session_id") != session_id
                    or document.get("request_id") != request_id
                ):
                    raise ValueError("Context artifact identity or schema mismatch")
                documents.append(document)
                if document["mode"] == "base":
                    break
                if document["mode"] != "append":
                    raise ValueError("Unsupported context segment")
                reference = AttachmentRef.from_dict(document["parent"])
            base = documents.pop()
            summary_refs = tuple(AttachmentRef.from_dict(r) for r in base["summary_refs"])
            fields = dict(base["epoch"])
            fields["summaries"] = [json.loads(self.artifacts.read_artifact(session_id, r)) for r in summary_refs]
            for document in reversed(documents):
                fields["items"].extend(document["items"])
                fields["source_digests"].extend(document["source_digests"])
                fields["state_digest"] = document["state_digest"]
            epoch = ContextEpoch.from_dict(fields)
            if (
                epoch.session_id != session_id
                or epoch.request_id != request_id
                or epoch.scope != scope
                or epoch.epoch_id != head["epoch_id"]
                or type(head["revision"]) is not int
                or head["revision"] < 1
            ):
                raise ValueError("Context head does not match its immutable data")
            return StoredContext(epoch, dict(head), tuple(references) + summary_refs, summary_refs)
        except PreparationWait:
            raise
        except (ValueError, TypeError, KeyError, OSError, BackupVerificationError, PathBoundaryError) as exc:
            raise PreparationWait("CONTEXT_RECOVERY_REQUIRED", "上下文或摘要附件无法原样恢复，请检查备份。") from exc

    def stage(self, candidate: ContextEpoch, previous: StoredContext | None = None) -> StoredContext:
        candidate.validate()
        summary_refs = ()
        references = []
        if previous is not None:
            validate_successor(previous.epoch, candidate)
            if candidate == previous.epoch:
                return previous
            summary_refs = previous.summary_refs
        for segment in candidate.summaries[len(summary_refs) :]:
            reference = self.artifacts.write_artifact(candidate.session_id, encode(segment.to_dict()).encode("utf-8"))
            summary_refs += (reference,)
        document = {
            "kind": "assistant_context",
            "schema_version": 1,
            "session_id": candidate.session_id,
            "request_id": candidate.request_id,
        }
        if previous is not None and candidate.epoch_id == previous.epoch.epoch_id:
            old = previous.epoch
            if (
                candidate.items[: len(old.items)] != old.items
                or candidate.source_digests[: len(old.source_digests)] != old.source_digests
                or replace(candidate, items=old.items, source_digests=old.source_digests, state_digest=old.state_digest)
                != old
            ):
                raise PreparationWait("CONTEXT_APPEND_CONFLICT", "同一上下文版本只能追加材料。")
            document.update(
                mode="append",
                parent=previous.head["artifact"],
                items=[i.to_dict() for i in candidate.items[len(old.items) :]],
                source_digests=list(candidate.source_digests[len(old.source_digests) :]),
                state_digest=candidate.state_digest,
            )
            references.extend(previous.references)
        else:
            fields = candidate.to_dict()
            fields.pop("summaries")
            document.update(mode="base", epoch=fields, summary_refs=[r.to_dict() for r in summary_refs])
        reference = self.artifacts.write_artifact(candidate.session_id, encode(document).encode("utf-8"))
        references.extend((*summary_refs, reference))
        head = {
            "schema_version": 1,
            "epoch_id": candidate.epoch_id,
            "revision": previous.head["revision"] + 1 if previous else 1,
            "artifact": reference.to_dict(),
        }
        return StoredContext(candidate, head, tuple({r.path: r for r in references}.values()), summary_refs)
