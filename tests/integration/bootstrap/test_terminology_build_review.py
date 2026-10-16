from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.terminology.story07_support import FixedClock, SequenceIds
from tests.application.terminology.test_build import _entry, _Parser, _snapshot, _source
from transbridge.application.contracts import JobRef, OperationResult, RequestContext
from transbridge.application.io import SourceSnapshot
from transbridge.application.terminology.drafts import revised_draft
from transbridge.application.terminology.errors import StaleBuildError
from transbridge.application.terminology.input_capture import SourceLease
from transbridge.application.terminology.models import BuildResult
from transbridge.bootstrap.terminology_build import BuildRunner
from transbridge.bootstrap.terminology_storage import (
    ProductionState,
    ProductionTerminologyCommitPort,
    RepositoryBaselines,
)
from transbridge.bootstrap.terminology_workloads import ProductionTerminologyCommands, PublishRunner
from transbridge.persistence.terminology.codec import dumps, loads
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository

pytestmark = pytest.mark.integration


class _Submit:
    def submit(self, request, owner):
        self.request = request
        return SimpleNamespace(ref=JobRef("job", owner.owner_id, "run"))


class _Harness:
    def __init__(self, repository, monkeypatch):
        self.repository = repository
        self.repositories = SimpleNamespace(for_project=lambda project_id: self.repository)
        self.snapshot = _snapshot((_source("source-a"),))
        self.parser = _Parser({"source-a": (_entry("source-a", "one", "Dragon", "龙"),)})
        monkeypatch.setattr(
            "transbridge.bootstrap.terminology_build.TranslationIoRegisteredSourceParser", lambda io: self.parser
        )
        self.lifecycle = SimpleNamespace(
            active=SimpleNamespace(
                project=SimpleNamespace(
                    envelope=SimpleNamespace(identity="project-1", revision=self.snapshot.project_revision)
                ),
                variant=SimpleNamespace(
                    ref=SimpleNamespace(identity=SimpleNamespace(value="main")), revision=self.snapshot.variant_revision
                ),
            )
        )
        self.state = ProductionState()
        self.submit = _Submit()
        self.ids = SequenceIds()
        self.context = RequestContext(
            "operator", project_id="project-1", variant_id="main", metadata=(("manual_actor_id", "operator"),)
        )
        self.runtime = SimpleNamespace(
            progress=SimpleNamespace(update=lambda value: None), checkpoint=lambda: None, cancellation=None
        )
        self.commands = ProductionTerminologyCommands(
            self.submit, self.repositories, None, self.state, self.ids, FixedClock(), self, self.lifecycle
        )
        self.commit = ProductionTerminologyCommitPort(
            self.lifecycle,
            self.repositories,
            self.state,
            SimpleNamespace(current_fingerprint=lambda lease: self.snapshot.sources[0].lease.actual_fingerprint),
        )

    def capture_build_input(self, context, *, config):
        baseline = RepositoryBaselines(self.repositories).capture_baseline("project-1", "main")
        return OperationResult.completed(
            replace(
                self.snapshot,
                effective_version_id=baseline.effective_version_id,
                effective_content_digest=baseline.effective_content_digest,
                draft_id=baseline.draft_id,
                draft_revision=baseline.draft_revision,
                draft_base_version_id=baseline.draft_base_version_id,
                draft_base_content_digest=baseline.draft_base_content_digest,
                decision_digest=baseline.decision_digest,
            )
        )

    def build(self):
        self.commands.start_build(self.capture_build_input(self.context, config={}).value, self.context)
        execution = BuildRunner(self.repositories, self.state, None, self.ids, self.lifecycle)(
            self.submit.request, self.runtime
        )
        assert self.commit.commit_if_current(self.submit.request.expected, execution.mutation).current
        return self.repository.active_draft("project-1", "main")

    def publish(self):
        self.commands.publish(self.context)
        execution = PublishRunner(self.repositories, self.state)(self.submit.request, self.runtime)
        assert self.commit.commit_if_current(self.submit.request.expected, execution.mutation).current
        return self.repository.effective_version("project-1", "main")

    def change_source(self):
        source = self.snapshot.sources[0]
        changed = SourceSnapshot.from_bytes(source.lease.snapshot.source, source.registration.format_id, b"changed")
        self.snapshot = replace(
            self.snapshot, sources=(replace(source, lease=SourceLease("source-a", changed, changed.sha256)),)
        )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        yield _Harness(repository, monkeypatch)
    finally:
        repository.close()


def test_real_name_candidate_builds_and_publishes_after_manual_edits(harness):
    draft = harness.build()
    assert [(item.original, item.translation) for item in draft.decisions] == [("Dragon", "龙")]
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="巨龙"
    )

    published = harness.publish()

    assert published.decisions[0].translation == "巨龙"
    assert published.manual_actions


def test_rebuild_preserves_published_manual_terms_suppression_and_no_evidence(harness):
    draft = harness.build()
    harness.commands.apply_decision("suppress", harness.context, term_id=draft.decisions[0].term_id)
    harness.commands.apply_decision("add", harness.context, original="Added", translation="手工词")
    first = harness.publish()
    harness.parser.entries = {"source-a": ()}
    harness.change_source()

    rebuilt = harness.build()
    second = harness.publish()

    assert {item.original for item in rebuilt.decisions} == {"Dragon", "Added"}
    assert all(item.evidence_ids == () for item in rebuilt.decisions)
    assert next(item for item in second.decisions if item.original == "Dragon").suppressed
    assert next(item for item in second.decisions if item.original == "Added").translation == "手工词"
    assert harness.repository.get_version(first.ref) == first


def test_rebuilding_reviewed_evidence_keeps_manual_translation_effective(harness):
    draft = harness.build()
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="人工译名"
    )
    first = harness.publish()

    rebuilt = harness.build()
    second = harness.publish()

    assert rebuilt.decisions == first.decisions
    assert second.decisions == first.decisions
    assert second.decisions[0].is_effective


def test_second_build_updates_existing_draft_and_accepts_new_candidates(harness):
    first = harness.build()
    harness.parser.entries["source-a"] += (_entry("source-a", "two", "Sword", "剑"),)
    harness.change_source()

    second = harness.build()
    published = harness.publish()

    assert second.ref.draft_id == first.ref.draft_id
    assert second.ref.revision == first.ref.revision + 1
    assert {item.original for item in published.decisions} == {"Dragon", "Sword"}


def test_changed_source_rejects_old_build_before_publish_submission(harness):
    harness.build()
    harness.change_source()

    with pytest.raises(StaleBuildError, match="重新构建"):
        harness.commands.publish(harness.context)
    assert harness.repository.effective_version("project-1", "main") is None
    assert harness.repository.active_draft("project-1", "main") is not None


def test_commit_guard_rejects_source_change_after_publish_submission(harness):
    harness.build()
    harness.commands.publish(harness.context)
    execution = PublishRunner(harness.repositories, harness.state)(harness.submit.request, harness.runtime)
    harness.change_source()

    result = harness.commit.commit_if_current(harness.submit.request.expected, execution.mutation)

    assert not result.current
    assert result.diagnostic == "TERMINOLOGY_SOURCE_CHANGED"
    assert harness.repository.effective_version("project-1", "main") is None


def test_persisted_proof_survives_repository_restart(harness, tmp_path):
    harness.build()
    original = harness.repository.latest_build("project-1", "main")
    harness.repository.close()
    reopened = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    harness.repository = reopened
    try:
        assert reopened.get_build(original.ref).input_digest == original.input_digest
        assert harness.publish().build_ref == original.ref
    finally:
        reopened.close()


def test_legacy_build_payload_is_readable_for_reports_but_requires_rebuild(harness):
    import json

    harness.build()
    result = harness.repository.latest_build("project-1", "main")
    legacy_result = replace(result, ref=replace(result.ref, build_key="legacy-build"))
    payload = json.loads(dumps(legacy_result))
    del payload["fields"]["input_digest"]
    legacy = json.dumps(payload)
    harness.repository._connection.execute(
        "INSERT INTO builds(build_key, content_digest, project_id, variant_id, payload_json) VALUES (?, ?, ?, ?, ?)",
        (legacy_result.ref.build_key, legacy_result.ref.content_digest, "project-1", "main", legacy),
    )
    harness.repository._connection.commit()
    assert loads(legacy, BuildResult).input_digest is None
    assert harness.repository.latest_build("project-1", "main").candidates == result.candidates
    with pytest.raises(StaleBuildError, match="重新构建"):
        harness.commands.publish(harness.context)


def test_build_and_draft_are_rolled_back_together_on_draft_write_failure(harness):
    from transbridge.application.terminology.drafts import DraftWriteExpectation
    from transbridge.application.terminology.errors import RevisionConflictError, TerminologyNotFoundError
    from transbridge.bootstrap.terminology_draft_access import LifecycleDraftLineReader

    current = harness.build()
    repository = harness.repository
    result = repository.latest_build("project-1", "main")
    extra = replace(result, ref=replace(result.ref, build_key="failing-build"))
    reader = LifecycleDraftLineReader(harness.lifecycle, repository)
    transactions = repository.draft_transactions(reader, build=extra)
    invalid = revised_draft(revised_draft(current))

    with pytest.raises(RevisionConflictError):
        transactions.save_draft(
            invalid, expectation=DraftWriteExpectation.from_draft(current, reader.read_line(None, "project-1", "main"))
        )
    with pytest.raises(TerminologyNotFoundError):
        repository.get_build(extra.ref)
    assert repository.active_draft("project-1", "main") == current
