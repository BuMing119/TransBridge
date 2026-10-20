"""The first session adopts materials prepared while its directory was loading."""

from threading import Event
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.smart_assistant.file_parser import ParsedDocument
from transbridge.ui.tools.smart_assistant.upload_binding import UploadBinding

_APP = QApplication.instance() or QApplication([])


def _until(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def materials(tmp_path, monkeypatch):
    parent = QWidget()
    notices, busy_changes, releases = [], [], []
    binding = UploadBinding(
        parent=parent, documents={}, notify=notices.append, max_bytes=1000, busy_changed=busy_changes.append
    )
    path = tmp_path / "reference.txt"
    path.write_text("Reference prepared before the session", encoding="utf-8")

    def pause_parse():
        entered, release = Event(), Event()
        releases.append(release)

        def parse(source):
            entered.set()
            assert release.wait(5)
            return ParsedDocument(source, "text", raw_text=path.read_text(encoding="utf-8"))

        monkeypatch.setattr(binding, "_parse", parse)
        binding.add_paths([path])
        _until(entered.is_set)
        return release

    yield SimpleNamespace(
        binding=binding, path=path, notices=notices, busy_changes=busy_changes, pause_parse=pause_parse
    )
    for release in releases:
        release.set()
    binding.close()
    binding._executor.shutdown(wait=True)
    parent.deleteLater()
    _APP.processEvents()


def test_first_session_keeps_parsed_material_and_later_sessions_are_isolated(materials):
    binding = materials.binding
    binding.add_paths([materials.path])
    _until(lambda: not binding.busy)
    expected = binding.snapshot()
    assert expected and expected[0]["content"] == "Reference prepared before the session"

    binding.activate("first")

    assert binding.snapshot() == expected
    binding.activate("second")
    assert binding.snapshot() is None
    binding.activate("first")
    assert binding.snapshot() == expected
    binding.activate(None)
    assert binding.snapshot() is None
    binding.activate("third")
    assert binding.snapshot() is None


@pytest.mark.parametrize("queued", [False, True])
def test_first_session_adopts_pending_and_queued_parse_results(materials, queued):
    binding = materials.binding
    release = materials.pause_parse()
    if queued:
        release.set()
        binding._executor.shutdown(wait=True)

    binding.activate("first")

    assert binding.busy
    assert materials.busy_changes[-1]
    with pytest.raises(ValueError, match="参考文件仍在提取文本"):
        binding.snapshot()
    release.set()
    binding._executor.shutdown(wait=True)
    _until(lambda: not binding.busy)
    assert binding.snapshot()[0]["content"] == "Reference prepared before the session"
    assert not materials.busy_changes[-1]
    assert not materials.notices


@pytest.mark.parametrize("detach", [False, True])
def test_adopted_parse_cannot_cross_later_session_switch(materials, detach):
    binding = materials.binding
    release = materials.pause_parse()
    binding.activate("first")
    if detach:
        binding.activate(None)
    binding.activate("second")
    release.set()
    binding._executor.shutdown(wait=True)
    _APP.processEvents()

    assert binding.snapshot() is None
    assert not binding.busy
    binding.activate("first")
    assert binding.snapshot() is None
    assert not materials.notices


@pytest.mark.parametrize("boundary", ["clear_before_binding", "clear_after_binding", "close_after_binding"])
def test_clear_and_close_fence_parse_results_across_initial_binding(materials, boundary):
    binding = materials.binding
    release = materials.pause_parse()
    if boundary == "clear_before_binding":
        binding.clear()
    binding.activate("first")
    if boundary == "clear_after_binding":
        binding.clear()
    elif boundary == "close_after_binding":
        binding.close()
    release.set()
    binding._executor.shutdown(wait=True)
    _APP.processEvents()

    assert not binding.documents
    assert not binding.busy
    assert binding.snapshot() is None
    assert not materials.notices
