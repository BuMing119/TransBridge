from __future__ import annotations

from dataclasses import replace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.smart_assistant.tools.test_source_import_authority import load_source, project as project_fixture
from transbridge.application.io import Stage
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.smart_assistant.tools import _dictionary_application as dictionary_module
from transbridge.smart_assistant.tools._project_tool_mutations import ProjectToolTarget
from transbridge.smart_assistant.tools.tool_migrator import _tool_apply_dictionary
from transbridge.smart_assistant.tools.tool_paratranz import _tool_upload_entries
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.translation_memory.manager import TranslationMemoryManager

_APP = QApplication.instance() or QApplication([])
_LOCALES = {"source_locale": "en", "target_locale": "zh-CN"}
project = project_fixture


def _install_dictionary(project, monkeypatch, entry, **overrides):
    manager = TranslationMemoryManager(project.root / "dictionary")
    values = {
        "source_locale": "en",
        "target_locale": "zh-CN",
        "source_namespace": entry.identity.namespace.value,
        "source_fingerprint": project.ctx.active_slot.source_snapshot.sha256,
        "enabled": True,
        "original": entry.original,
    }
    values.update(overrides)
    manager.add(entry.key, translation="Dictionary value", mod_file_id="fixture", stage=1, **values)
    manager.save()
    monkeypatch.setattr(dictionary_module, "TranslationMemoryManager", lambda: manager)
    return manager


@pytest.mark.parametrize(
    "override",
    [
        {"enabled": False},
        {"source_locale": "ja"},
        {"target_locale": "fr"},
        {"source_namespace": "foreign-source", "original": "Different original"},
        {"source_fingerprint": "f" * 64},
        {"original": "Stale original"},
    ],
)
def test_dictionary_excludes_disabled_wrong_locale_foreign_or_stale_candidates(project, monkeypatch, override):
    entry = load_source(project)
    _install_dictionary(project, monkeypatch, entry, **override)
    before = project.services.project_lifecycle.active.variant.snapshot()

    result = _tool_apply_dictionary(_LOCALES, ExecutionContext(app_context=project.ctx))

    assert result.data["applied"] == 0
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert next(iter(project.ctx.collection)).translation == ""
    assert not project.ctx.authoritative_projection_diverged()


def test_dictionary_candidate_commits_to_v2_and_survives_save(project, monkeypatch):
    entry = load_source(project)
    _install_dictionary(project, monkeypatch, entry)

    result = _tool_apply_dictionary(_LOCALES, ExecutionContext(app_context=project.ctx))

    assert result.success, result.message
    assert result.data["applied"] == 1 and result.data["key_hits"] == 1
    assert next(iter(project.ctx.collection)).translation == "Dictionary value"
    active = project.services.project_lifecycle.active
    assert active.variant.snapshot().entries[0].translation == "Dictionary value"
    assert active.variant.snapshot().entries[0].stage is Stage.TRANSLATED
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.gui_project_commands.save(project.request).is_success
    persisted = project.services.variants.load(active.formal_variant_ref).value.envelope.data
    assert persisted["entries"][0]["translation"] == "Dictionary value"


def test_dictionary_rejects_unknown_query_locale_without_mutating_v2(project, monkeypatch):
    entry = load_source(project)
    _install_dictionary(project, monkeypatch, entry)
    before = project.services.project_lifecycle.active.variant.snapshot()

    result = _tool_apply_dictionary({}, ExecutionContext(app_context=project.ctx))

    assert not result.success
    assert "source_locale" in result.message
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert next(iter(project.ctx.collection)).translation == ""


def test_dictionary_late_revision_does_not_overwrite_newer_translation(project, monkeypatch):
    entry = load_source(project)
    _install_dictionary(project, monkeypatch, entry)
    query = dictionary_module.TranslationMemoryQueryService.query

    def concurrent_query(service, request, cancellation=None):
        selected = query(service, request, cancellation)
        ProjectToolTarget.capture(ExecutionContext(app_context=project.ctx)).commit_records((
            replace(entry, translation="Concurrent edit", stage=1),
        ))
        return selected

    monkeypatch.setattr(dictionary_module.TranslationMemoryQueryService, "query", concurrent_query)
    result = _tool_apply_dictionary(_LOCALES, ExecutionContext(app_context=project.ctx))

    assert not result.success
    assert project.services.project_lifecycle.active.variant.snapshot().entries[0].translation == "Concurrent edit"
    assert next(iter(project.ctx.collection)).translation == "Concurrent edit"
    assert not project.ctx.authoritative_projection_diverged()


class _RemoteService:
    def __init__(self, *, failed_indices=(), before_return=None):
        self.failed_indices = set(failed_indices)
        self.before_return = before_return
        self.received = []
        self.closed = False

    def upsert_entry(self, project_id, entry, *, force_overwrite=False, cancellation=None):
        assert isinstance(entry, ParaTranzEntry)
        self.received.append((project_id, entry, force_overwrite))
        if len(self.received) in self.failed_indices:
            raise RuntimeError("Remote service rejected this entry")
        if self.before_return is not None:
            self.before_return()
        return replace(entry, remote_id=entry.remote_id or 700 + len(self.received))

    def close(self):
        self.closed = True


def _bind_remote(monkeypatch, remote):
    monkeypatch.setattr("transbridge.paratranz.service.ParaTranzService.from_config", lambda _config: remote)


def _two_entries(project):
    xml = project.source.read_text(encoding="utf-8")
    block = xml[xml.index("<ESP>") : xml.index("</ESP>") + len("</ESP>")]
    second = block.replace("GreetingTopic", "SecondTopic").replace("00000001", "00000002")
    project.source.write_text(xml.replace("</DocumentElement>", second + "</DocumentElement>"), encoding="utf-8")
    load_source(project)
    return tuple(project.ctx.collection)


def test_typed_upload_remote_id_is_committed_saved_and_used_for_next_forced_update(project, monkeypatch):
    load_source(project)
    remote = _RemoteService()
    _bind_remote(monkeypatch, remote)
    context = ExecutionContext(app_context=project.ctx)

    first = _tool_upload_entries({"project_id": 7}, context)

    assert first.success and not first.partial
    assert first.data["uploaded"] == 1
    assert remote.closed
    local = next(iter(project.ctx.collection))
    assert [(ref.scope, ref.opaque_id) for ref in local.external_refs] == [("project:7", 701)]
    assert not project.ctx.authoritative_projection_diverged()
    active = project.services.project_lifecycle.active
    assert active.variant.snapshot().entries[0].external_refs == local.external_refs
    assert project.services.gui_project_commands.save(project.request).is_success
    persisted = project.services.variants.load(active.formal_variant_ref).value.envelope.data
    assert persisted["entries"][0]["external_refs"][0]["opaque_id"] == 701

    second = _tool_upload_entries({"project_id": 7, "force_overwrite": True}, context)

    assert second.success
    assert remote.received[-1][1].remote_id == 701
    assert remote.received[-1][2] is True
    assert len(next(iter(project.ctx.collection)).external_refs) == 1
    assert not project.ctx.authoritative_projection_diverged()


@pytest.mark.parametrize("failed_indices,expected_uploaded,partial", [({2}, 1, True), ({1, 2}, 0, False)])
def test_upload_partial_and_all_failed_results_match_authoritative_commits(
    project, monkeypatch, failed_indices, expected_uploaded, partial
):
    _two_entries(project)
    remote = _RemoteService(failed_indices=failed_indices)
    _bind_remote(monkeypatch, remote)

    result = _tool_upload_entries({"project_id": 7}, ExecutionContext(app_context=project.ctx))

    assert not result.success
    assert result.partial is partial
    assert result.data["uploaded"] == expected_uploaded
    assert len(result.failed_items) == 2 - expected_uploaded
    states = project.services.project_lifecycle.active.variant.snapshot().entries
    assert sum(bool(entry.external_refs) for entry in states) == expected_uploaded
    assert not project.ctx.authoritative_projection_diverged()
    assert remote.closed


def test_upload_late_revision_reports_remote_side_effect_without_overwriting_current_state(project, monkeypatch):
    entries = _two_entries(project)

    def concurrent_edit():
        ProjectToolTarget.capture(ExecutionContext(app_context=project.ctx)).commit_records((
            replace(entries[0], translation="Concurrent edit", stage=1),
        ))

    remote = _RemoteService(before_return=concurrent_edit)
    _bind_remote(monkeypatch, remote)

    result = _tool_upload_entries({"project_id": 7}, ExecutionContext(app_context=project.ctx))

    assert not result.success and result.partial
    assert result.failed_items[0]["remote_id"] == 701
    assert len(remote.received) == 1
    states = project.services.project_lifecycle.active.variant.snapshot().entries
    assert states[0].translation == "Concurrent edit"
    assert not any(entry.external_refs for entry in states)
    assert not project.ctx.authoritative_projection_diverged()
