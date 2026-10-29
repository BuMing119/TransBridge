"""Download presentation and merge contracts have no dependency on Qt or GUI adapters."""

import subprocess
import sys


def test_download_application_contracts_import_without_loading_qt_or_gui():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import transbridge.application.sync.review_view; "
            "import transbridge.application.sync.download_view; "
            "import transbridge.application.sync.download_merge; "
            "import transbridge.application.sync.project_download; "
            "import transbridge.application.sync.download_context; "
            "import transbridge.application.sync.download_run; "
            "loaded = [name for name in sys.modules "
            "if name == 'transbridge.ui' or name.startswith(('transbridge.ui.', 'PyQt6'))]; "
            "assert not loaded, loaded",
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_gui_download_contracts_reexport_the_same_application_objects():
    from transbridge.application.sync import download_merge, download_view, review_view
    from transbridge.ui.operations import (
        batch_download_results,
        batch_download_view,
        sync_review_projection,
        sync_review_view,
    )

    for name in ("KEEP_LOCAL", "SyncReviewCandidate", "SyncReviewRow"):
        assert getattr(sync_review_view, name) is getattr(review_view, name)
    for name in ("review_rows", "_candidate_label"):
        assert getattr(sync_review_projection, name) is getattr(review_view, name)
    for name in ("DownloadPreview", "DownloadReceipt", "DownloadSourceOption"):
        assert getattr(batch_download_view, name) is getattr(download_view, name)
    for name in ("result_changes", "publish_results", "merge_options", "preview_entry_states", "merge_review_rows"):
        assert getattr(batch_download_results, name) is getattr(download_merge, name)


def test_infra_markdown_renderer_keeps_its_explicit_public_import():
    from transbridge.infra import MarkdownRenderer
    from transbridge.infra.markdown_renderer import MarkdownRenderer as ConcreteMarkdownRenderer

    assert MarkdownRenderer is ConcreteMarkdownRenderer
