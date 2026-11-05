"""The project download behavior belongs to the application, not a GUI controller."""

import json
import logging
from pathlib import Path

import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_session import case as case, compare, states  # noqa: F401
from transbridge.application.sync.download_run import run_download
from transbridge.application.sync.project_download import ProjectDownloadSession
from transbridge.paratranz.batch_download_snapshot import BatchDownloadSnapshotReader
from transbridge.paratranz.file_transport import UploadCancellation


def inline_execute(context, _fingerprint, operation, cancel):
    return operation(context, lambda mutation: mutation(), UploadCancellation(cancel))


def test_application_use_case_reads_matches_saves_and_discards_without_gui_execution(case):  # noqa: F811
    session = ProjectDownloadSession(
        case.context,
        execute=inline_execute,
        reader_factory=lambda config: BatchDownloadSnapshotReader(config, gateway_factory=lambda _: case.gateway),
        service_factory=case.session.service_factory,
    )
    sources = tuple(source.source_id for source in case.sources)
    result = run_download(sources, "keep_current", (), session.compare, session.apply, lambda *_: None, case.cancel)
    assert result.updated == 2 and not result.requires_review
    assert {translation for translation, _ in states(case).values()} == {"云端1", "云端2"}
    active = case.services.project_lifecycle.active
    assert not active.dirty
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    assert session.plan is None and not session._reads and not session._bases
    assert not list(Path(case.services.root).rglob("download-results"))
    assert case.gateway.calls == [("inventory", 42), ("file", 1), ("file", 2)]


def test_fifty_file_archive_matches_saves_and_releases_temporary_content(case):  # noqa: F811
    from tests.paratranz.test_artifact_file_downloads import ExportAPI
    from transbridge.paratranz.artifact_download import FreshArtifactDownload
    from transbridge.paratranz.artifact_file_reads import ArtifactFileReads

    first = next(iter(case.gateway.files)).removesuffix(".json")
    for index in range(3, 51):
        case.gateway.files[f"{first}_{index}.json"] = index
        case.gateway.rows[index] = []
    api = ExportAPI({path: case.gateway.rows[file_id] for path, file_id in case.gateway.files.items()})

    def artifacts(config):
        return ArtifactFileReads(
            config,
            downloader_factory=lambda config: FreshArtifactDownload(
                config, api_factory=lambda **_: api, poll_interval=0.001
            ),
        )

    session = ProjectDownloadSession(
        case.context,
        execute=inline_execute,
        reader_factory=lambda config: BatchDownloadSnapshotReader(
            config, gateway_factory=lambda _: case.gateway, artifact_factory=artifacts
        ),
        service_factory=case.session.service_factory,
    )
    sources = tuple(source.source_id for source in case.sources)
    result = run_download(sources, "keep_current", (), session.compare, session.apply, lambda *_: None, case.cancel)
    assert result.updated == 2 and not result.requires_review
    assert set(states(case).values()) == {("云端1", 3), ("云端2", 3)}
    active = case.services.project_lifecycle.active
    assert not active.dirty
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    assert session.plan is None and not session._reads and not session._bases
    assert api.closed and not api.destination.parent.exists()
    assert case.gateway.calls == [("inventory", 42)]


def test_same_plan_candidates_are_reused_but_new_local_edit_is_still_protected(case, monkeypatch):  # noqa: F811
    from transbridge.application.sync import project_download

    build, plans = project_download.result_changes, []

    def counted(plan):
        plans.append(plan)
        return build(plan)

    monkeypatch.setattr(project_download, "result_changes", counted)
    assert compare(case).updates == 2
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="本轮新编辑", stage=1)
    receipt = case.session.apply(lambda *_: None, case.cancel)
    assert receipt.updated == 1
    assert states(case)[key] == ("本轮新编辑", 1)
    assert states(case)[case.sources[1].entries[0].identity] == ("云端2", 3)
    assert len(plans) == 1
    assert case.session._candidate_plan is None and case.session._candidates == ()


def test_replaced_plan_rebuilds_candidates_and_discard_releases_them(case, monkeypatch):  # noqa: F811
    from transbridge.application.sync import project_download

    build, plans = project_download.result_changes, []

    def counted(plan):
        plans.append(plan)
        return build(plan)

    monkeypatch.setattr(project_download, "result_changes", counted)
    source = case.sources[0].source_id
    assert compare(case, (source,)).updates == 1
    first = case.session._result_changes()
    assert len(first) == 1
    assert compare(case).updates == 2
    second = case.session._result_changes()
    assert len(second) == 2 and second is not first
    assert compare(case).updates == 2
    assert len(plans) == 3 and plans[1] is not plans[2]
    assert case.gateway.calls == [("inventory", 42), ("file", 1), ("inventory", 42), ("file", 2)]
    case.session.discard()
    assert case.session._candidate_plan is None and case.session._candidates == ()
    assert compare(case, (source,)).updates == 1 and len(plans) == 4
    assert case.gateway.calls[-2:] == [("inventory", 42), ("file", 1)]


def test_download_phase_timings_separate_capture_network_match_preview_and_save(case, caplog):  # noqa: F811
    logger = "transbridge.application.categorized_upload.diagnostics"
    with caplog.at_level(logging.INFO, logger=logger):
        assert compare(case).updates == 2
        assert case.session.apply(lambda *_: None, case.cancel).updated == 2
    phases = [
        payload
        for record in caplog.records
        if record.name == logger and (payload := json.loads(record.getMessage())).get("step") == "phase_timing"
    ]
    assert [row["stage"] for row in phases] == [
        "download_capture",
        "download_fetch",
        "download_assemble",
        "download_match",
        "download_preview",
        "download_merge_save",
    ]
    assert len({row["operation_id"] for row in phases}) == 1
    assert all(row["outcome"] == "completed" and row["elapsed_ms"] >= 0 for row in phases)
    assert all("云端1" not in json.dumps(row, ensure_ascii=False) for row in phases)


def test_failed_download_phase_is_timed_without_logging_exception_text(case, monkeypatch, caplog):  # noqa: F811
    logger = "transbridge.application.categorized_upload.diagnostics"

    def fail(*_args, **_kwargs):
        raise RuntimeError("private capture text")

    monkeypatch.setattr(case.context.project_commands, "file_sources_snapshot", fail)
    with caplog.at_level(logging.INFO, logger=logger), pytest.raises(RuntimeError, match="private capture"):
        compare(case)
    rows = [json.loads(record.getMessage()) for record in caplog.records if record.name == logger]
    (row,) = [row for row in rows if row.get("step") == "phase_timing"]
    assert row["stage"] == "download_capture" and row["outcome"] == "failed"
    assert row["elapsed_ms"] >= 0 and "private capture text" not in caplog.text
    assert case.session._candidate_plan is None and case.session._candidates == ()


def test_timing_failure_after_commit_preserves_success_receipt(case, monkeypatch, caplog):  # noqa: F811
    from transbridge.application.categorized_upload import diagnostics

    log_event = diagnostics.log_event

    def fail_after_save(event, **fields):
        if event == "phase_timing" and fields.get("stage") == "download_merge_save":
            raise ValueError("private diagnostic failure")
        return log_event(event, **fields)

    monkeypatch.setattr(diagnostics, "log_event", fail_after_save)
    assert compare(case).updates == 2
    receipt = case.session.apply(lambda *_: None, case.cancel)
    assert receipt.updated == 2 and not receipt.requires_review
    assert {value[0] for value in states(case).values()} == {"云端1", "云端2"}
    active = case.services.project_lifecycle.active
    assert not active.dirty
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    assert "timing diagnostics unavailable" in caplog.text
    assert "private diagnostic failure" not in caplog.text


def test_timing_failure_preserves_original_phase_error(case, monkeypatch, caplog):  # noqa: F811
    from transbridge.application.categorized_upload import diagnostics

    expected = RuntimeError("original capture failure")

    def fail_capture(*_args, **_kwargs):
        raise expected

    def fail_log(*_args, **_kwargs):
        raise ValueError("private diagnostic failure")

    monkeypatch.setattr(case.context.project_commands, "file_sources_snapshot", fail_capture)
    monkeypatch.setattr(diagnostics, "log_event", fail_log)
    with pytest.raises(RuntimeError) as caught:
        compare(case)
    assert caught.value is expected
    assert "timing diagnostics unavailable" in caplog.text
    assert "private diagnostic failure" not in caplog.text
