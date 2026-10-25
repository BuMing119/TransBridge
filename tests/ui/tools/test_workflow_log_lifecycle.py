from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore
from transbridge.ui.tools.ai_translator.workflow_logging_client import WorkflowLoggingLLMClient


@pytest.fixture
def opened_files(monkeypatch):
    opened = []
    original_open = Path.open

    def open_file(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_file)
    return opened


def test_many_channels_bound_live_handles_and_append_after_eviction(tmp_path, opened_files):
    store = WorkflowLogStore(None, workflow="test", log_dir=tmp_path)
    (tmp_path / "channel_0.log").write_text("old run must be replaced", encoding="utf-8")
    try:
        for index in range(1000):
            store.write_chunk(f"channel_{index}", f"first-{index}\n")
            assert sum(not handle.closed for handle in opened_files) <= store.MAX_OPEN_FILES
        store.write_chunk("channel_0", "last\n")
        store.flush()
        assert (tmp_path / "channel_0.log").read_text(encoding="utf-8") == "first-0\nlast\n"
    finally:
        store.close()
    assert all(handle.closed for handle in opened_files)


def test_parallel_streams_survive_eviction_and_channel_reopening(tmp_path, monkeypatch):
    monkeypatch.setattr(WorkflowLogStore, "MAX_OPEN_FILES", 4)
    store = WorkflowLogStore(None, workflow="test", log_dir=tmp_path)

    def stream(index):
        for chunk in range(30):
            store.write_line(f"stream_{index}", str(chunk))
            if chunk == 12:
                store.close_channel(f"stream_{index}")

    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(stream, range(30)))
    finally:
        store.close()
    for index in range(30):
        assert (tmp_path / f"stream_{index}.log").read_text(encoding="utf-8") == "".join(
            f"{chunk}\n" for chunk in range(30)
        )


@pytest.mark.parametrize("method", ["chat", "chat_prepared", "chat_stream", "chat_stream_prepared"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_call_releases_file_before_task_end_for_all_call_paths(tmp_path, opened_files, method, outcome):
    class Cancelled(BaseException):
        pass

    failure = Cancelled("cancelled") if outcome == "cancelled" else RuntimeError("provider failed")

    class Client:
        def chat(self, messages, max_tokens=0):
            if outcome != "success":
                raise failure
            return "response"

        def chat_stream(self, messages, max_tokens, callback):
            callback("response")
            return self.chat(messages, max_tokens)

    store = WorkflowLogStore(None, workflow="test", log_dir=tmp_path)
    client = WorkflowLoggingLLMClient(Client(), store)
    messages = [{"role": "user", "content": "request"}]
    args = [lambda: messages] if "prepared" in method else [messages]
    if "stream" in method:
        args.extend([10, lambda chunk: None])
    try:
        if outcome == "success":
            assert getattr(client, method)(*args) == "response"
        else:
            with pytest.raises(type(failure)) as caught:
                getattr(client, method)(*args)
            assert caught.value is failure
        assert opened_files and all(handle.closed for handle in opened_files)
        if outcome != "cancelled":
            text = (tmp_path / "llm_call_001.log").read_text(encoding="utf-8")
            assert "[END CALL]" in text
            assert ("[ERROR]" in text) == (outcome == "failure")
    finally:
        store.close()


def test_preparation_error_releases_reserved_call_log(tmp_path, opened_files):
    store = WorkflowLogStore(None, workflow="test", log_dir=tmp_path)
    client = WorkflowLoggingLLMClient(SimpleNamespace(chat=lambda *_args: "unused"), store)

    def prepare():
        raise ValueError("invalid input")

    try:
        with pytest.raises(ValueError, match="invalid input"):
            client.chat_prepared(prepare)
        assert all(handle.closed for handle in opened_files)
    finally:
        store.close()


def test_grouped_calls_close_and_append_without_truncating(tmp_path, opened_files):
    class Client:
        def chat(self, messages, max_tokens=0):
            return messages[0]["content"]

    store = WorkflowLogStore(None, workflow="test", log_dir=tmp_path)
    client = WorkflowLoggingLLMClient(Client(), store, grouped=True)
    try:
        for index in range(20):
            assert client.chat([{"content": f"response-{index}"}]) == f"response-{index}"
            assert all(handle.closed for handle in opened_files)
        text = (tmp_path / "llm_call.log").read_text(encoding="utf-8")
        assert text.count("[END CALL]") == 20
        assert "[CALL 001]" in text and "[CALL 020]" in text
    finally:
        store.close()


def test_open_failure_releases_other_channels_and_preserves_diagnostic(tmp_path, opened_files, monkeypatch):
    store = WorkflowLogStore(None, workflow="test", log_dir=tmp_path)
    store.write_line("first", "written")
    original_open = Path.open

    def fail_one(path, *args, **kwargs):
        if path.name == "second.log":
            raise OSError("disk unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_one)
    store.write_line("second", "failed")
    store.close()
    assert not store.is_available
    assert "disk unavailable" in store.last_error
    assert all(handle.closed for handle in opened_files)
    store.close()
