"""The project download behavior belongs to the application, not a GUI controller."""

from pathlib import Path

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_session import case as case, states  # noqa: F401
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
