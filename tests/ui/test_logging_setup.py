from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from transbridge import __version__
from transbridge.application.security.diagnostic_logging import diagnostic_credentials
from transbridge.ui import app as app_module


@pytest.fixture
def isolated_logging(monkeypatch, tmp_path):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    for handler in original_handlers:
        root.removeHandler(handler)
    monkeypatch.setattr(app_module, "_file_log_handler", None)
    monkeypatch.setattr(app_module.ParatranzConfig, "get_data_dir", lambda: str(tmp_path))
    try:
        yield root
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        if app_module._file_log_handler is not None:
            app_module._file_log_handler.close()
        root.setLevel(original_level)
        for handler in original_handlers:
            root.addHandler(handler)


def _log_text(tmp_path: Path) -> str:
    (log_file,) = (tmp_path / "log").glob("*.log")
    return log_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("preconfigured", [False, True])
def test_setup_writes_immediately_with_or_without_existing_handlers(isolated_logging, tmp_path, preconfigured):
    root = isolated_logging
    # Pytest reinstalls capture handlers between fixture setup and the test call.
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    console = io.StringIO()
    existing = logging.StreamHandler(console)
    if preconfigured:
        root.addHandler(existing)
        root.setLevel(logging.CRITICAL)

    app_module._setup_logging()
    logging.getLogger("transbridge.test.logging").debug("debug event")
    logging.getLogger("transbridge.test.logging").error("writeback failed")

    text = _log_text(tmp_path)
    assert f"TransBridge {__version__} GUI logging initialized:" in text
    assert str(tmp_path / "log") in text
    assert "debug event" in text
    assert "writeback failed" in text
    if preconfigured:
        assert existing in root.handlers
        assert "writeback failed" in console.getvalue()
        assert not console.closed


def test_repeated_setup_reuses_file_without_duplicate_records(isolated_logging, tmp_path):
    app_module._setup_logging()
    original = app_module._file_log_handler
    app_module._setup_logging()
    app_module._setup_logging()
    logging.getLogger("transbridge.test.logging").warning("one warning")

    assert app_module._file_log_handler is original
    assert [handler for handler in isolated_logging.handlers if isinstance(handler, logging.FileHandler)] == [original]
    text = _log_text(tmp_path)
    assert text.count("one warning") == 1
    assert text.count("GUI logging initialized:") == 1


def test_setup_closes_owned_handler_when_data_directory_changes(isolated_logging, monkeypatch, tmp_path):
    app_module._setup_logging()
    previous = app_module._file_log_handler
    changed_directory = tmp_path / "changed"
    monkeypatch.setattr(app_module.ParatranzConfig, "get_data_dir", lambda: str(changed_directory))

    app_module._setup_logging()
    logging.getLogger("transbridge.test.logging").warning("new directory event")

    assert previous.stream is None
    assert previous not in isolated_logging.handlers
    assert "new directory event" in _log_text(changed_directory)
    assert "new directory event" not in _log_text(tmp_path)


def test_file_logging_retains_credential_protection(isolated_logging, tmp_path):
    app_module._setup_logging()
    with diagnostic_credentials("opaque-test-credential"):
        try:
            raise RuntimeError("provider rejected opaque-test-credential")
        except RuntimeError:
            logging.getLogger("transbridge.test.logging").exception("request failed: api_key=secret-test-value")

    text = _log_text(tmp_path)
    assert "opaque-test-credential" not in text
    assert "secret-test-value" not in text
    assert "RuntimeError" in text
    assert "request failed" in text
