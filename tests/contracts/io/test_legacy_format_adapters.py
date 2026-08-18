from __future__ import annotations

from dataclasses import replace
import importlib
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    EetXmlAdapter,
    EntryKey,
    FormatId,
    ParseRequest,
    SourceDescriptor,
    SourceNamespace,
    SsePluginAdapter,
    TranslationIoUseCase,
    WriteRequest,
    XtXmlAdapter,
)
from transbridge.application.tasks.controls import CancellationToken as TaskCancellationToken
from transbridge.entrypoints.agent import parse_translation_source as agent_parse
from transbridge.entrypoints.gui import parse_translation_source as gui_parse
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_eet

FIXTURES = Path(__file__).with_name("fixtures")
ESP_FIXTURE = Path("tests/parser/data/sample.esp")


def _request(path: Path, format_id: FormatId, *, namespace=None) -> ParseRequest:
    return ParseRequest(
        SourceDescriptor(str(path), path.name, path.stat().st_size),
        RequestContext("io-contract", run_id="run-s04"),
        format_id,
        namespace,
    )


def _write_request(path: Path, format_id: FormatId, parsed, entries) -> WriteRequest:
    return WriteRequest(
        SourceDescriptor(str(path), path.name),
        format_id,
        tuple(entries),
        1,
        RequestContext("io-contract", run_id="run-s04"),
        source_snapshot=parsed.source_snapshot,
    )


@pytest.mark.parametrize(
    ("adapter", "fixture", "format_id", "translation"),
    [
        (EetXmlAdapter(), "eet-small.xml", FormatId.XML_EET, "你好，旅行者。"),
        (XtXmlAdapter(), "xt-small.xml", FormatId.XML_XT, "欢迎，旅行者。"),
    ],
)
def test_xml_adapter_real_parse_modify_write_reparse_chain(
    tmp_path: Path,
    adapter,
    fixture: str,
    format_id: FormatId,
    translation: str,
) -> None:
    source = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, source)
    parsed = adapter.parse(_request(source, format_id))
    assert parsed.outcome is OperationOutcome.COMPLETED
    assert len(parsed.entries) == 1
    changed = replace(parsed.entries[0], translation=translation, stage=1)
    target = tmp_path / f"out-{fixture}"

    written = adapter.write(_write_request(target, format_id, parsed, (changed,)))
    reparsed = adapter.parse(_request(target, format_id, namespace=changed.identity.namespace))

    assert written.outcome is OperationOutcome.COMPLETED
    assert reparsed.outcome is OperationOutcome.COMPLETED
    assert reparsed.entries[0].identity == changed.identity
    assert reparsed.entries[0].translation == translation
    assert reparsed.entries[0].original == changed.original


def test_plugin_real_parse_modify_write_reparse_chain(tmp_path: Path) -> None:
    source = tmp_path / "small-real-chain.esp"
    shutil.copyfile(ESP_FIXTURE, source)
    adapter = SsePluginAdapter()
    parsed = adapter.parse(_request(source, FormatId.PLUGIN_SSE))

    assert parsed.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}
    assert parsed.entries
    changed = replace(parsed.entries[0], translation="Adapter smoke translation", stage=1)
    target = tmp_path / "translated.esp"

    written = adapter.write(_write_request(target, FormatId.PLUGIN_SSE, parsed, (changed,)))
    reparsed = adapter.parse(_request(target, FormatId.PLUGIN_SSE, namespace=changed.identity.namespace))

    assert written.outcome is OperationOutcome.COMPLETED
    assert target.exists()
    assert reparsed.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}
    matching = [entry for entry in reparsed.entries if entry.identity == changed.identity]
    assert len(matching) == 1
    assert matching[0].original == "Adapter smoke translation"


def test_source_fingerprint_change_blocks_blind_write(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURES / "eet-small.xml", source)
    adapter = EetXmlAdapter()
    parsed = adapter.parse(_request(source, FormatId.XML_EET))
    changed = replace(parsed.entries[0], translation="changed", stage=1)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    target = tmp_path / "must-not-exist.xml"

    result = adapter.write(_write_request(target, FormatId.XML_EET, parsed, (changed,)))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SOURCE_FINGERPRINT_CONFLICT"
    assert not target.exists()


def test_eet_utf8_bom_is_preserved_on_staged_write(tmp_path: Path) -> None:
    source = tmp_path / "bom-source.xml"
    source.write_bytes(b"\xef\xbb\xbf" + (FIXTURES / "eet-small.xml").read_bytes())
    adapter = EetXmlAdapter()
    parsed = adapter.parse(_request(source, FormatId.XML_EET))
    changed = replace(parsed.entries[0], translation="BOM preserved", stage=1)
    target = tmp_path / "bom-output.xml"

    result = adapter.write(_write_request(target, FormatId.XML_EET, parsed, (changed,)))

    assert result.outcome is OperationOutcome.COMPLETED
    assert parsed.source_snapshot.bom == b"\xef\xbb\xbf"
    assert parsed.source_snapshot.encoding == "utf-8"
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    assert adapter.parse(_request(target, FormatId.XML_EET)).entries[0].translation == "BOM preserved"


def test_real_task_cancellation_token_blocks_write_before_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURES / "eet-small.xml", source)
    adapter = EetXmlAdapter()
    parsed = adapter.parse(_request(source, FormatId.XML_EET))
    target = tmp_path / "must-not-exist.xml"
    token = TaskCancellationToken()
    token._cancel("contract-test")
    request = replace(
        _write_request(target, FormatId.XML_EET, parsed, parsed.entries),
        cancellation=token,
    )

    result = adapter.write(request)

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.counts.cancelled == 1
    assert result.diagnostics[0].code == "WRITE_CANCELLED"
    assert not target.exists()


def test_missing_or_ambiguous_locator_fails_without_artifact(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURES / "xt-small.xml", source)
    adapter = XtXmlAdapter()
    parsed = adapter.parse(_request(source, FormatId.XML_XT))
    missing = replace(
        parsed.entries[0],
        entry_key=EntryKey(parsed.entries[0].identity.namespace, "missing-locator"),
        key="missing-locator",
    )
    target = tmp_path / "must-not-exist.xml"

    result = adapter.write(_write_request(target, FormatId.XML_XT, parsed, (missing,)))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SOURCE_LOCATOR_CONFLICT"
    assert dict(result.diagnostics[0].details)["record_index"] == 0
    assert not target.exists()


@pytest.mark.parametrize(
    ("adapter", "format_id", "content"),
    [
        (EetXmlAdapter(), FormatId.XML_EET, b"<DocumentElement><ESP>"),
        (XtXmlAdapter(), FormatId.XML_XT, b"<SSTXMLRessources><Content>"),
        (SsePluginAdapter(), FormatId.PLUGIN_SSE, b"not-a-plugin"),
    ],
)
def test_damaged_source_is_failed_not_completed_empty(tmp_path: Path, adapter, format_id, content: bytes) -> None:
    source = tmp_path / f"damaged-{format_id.value}"
    source.write_bytes(content)

    result = adapter.parse(_request(source, format_id))

    assert result.outcome is OperationOutcome.FAILED
    assert result.entries == () and result.source_snapshot is None
    assert result.stats.failed == 1


def test_gui_and_agent_delegate_to_same_parse_use_case(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURES / "eet-small.xml", source)
    request = _request(source, FormatId.XML_EET)
    use_case = TranslationIoUseCase()

    gui_result = gui_parse(use_case, request)
    agent_result = agent_parse(use_case, request)

    assert gui_result.outcome is OperationOutcome.COMPLETED
    assert agent_result == gui_result
    assert gui_result.source_snapshot.sha256 == agent_result.source_snapshot.sha256


def test_converter_leaf_import_does_not_cycle_through_legacy_adapters() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import transbridge.converter.translation_entry"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_public_adapter_exports_remain_lazy_and_compatible() -> None:
    io_package = importlib.import_module("transbridge.application.io")

    assert io_package.EetXmlAdapter is EetXmlAdapter
    assert io_package.SsePluginAdapter is SsePluginAdapter
    assert io_package.XtXmlAdapter is XtXmlAdapter


def test_eet_duplicate_source_locator_is_failed_with_index_diagnostics(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.xml"
    fixture = (FIXTURES / "eet-small.xml").read_text(encoding="utf-8")
    esp = fixture[fixture.index("  <ESP>") : fixture.index("  </ESP>") + len("  </ESP>")]
    source.write_text(fixture.replace("</DocumentElement>", f"{esp}\n</DocumentElement>"), encoding="utf-8")

    result = EetXmlAdapter().parse(_request(source, FormatId.XML_EET))

    assert result.outcome is OperationOutcome.FAILED
    assert result.stats.failed == 2
    assert [item.code for item in result.diagnostics] == [
        "SOURCE_LOCATOR_CONFLICT",
        "SOURCE_LOCATOR_CONFLICT",
    ]
    assert {dict(item.details)["record_index"] for item in result.diagnostics} == {0, 1}


def test_xml_write_rejects_entry_from_another_source_namespace(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    other = tmp_path / "other.xml"
    shutil.copyfile(FIXTURES / "eet-small.xml", source)
    shutil.copyfile(FIXTURES / "eet-small.xml", other)
    adapter = EetXmlAdapter()
    parsed = adapter.parse(_request(source, FormatId.XML_EET))
    foreign = adapter.parse(
        _request(
            other,
            FormatId.XML_EET,
            namespace=SourceNamespace.from_fingerprint("xml.eet", "f" * 64),
        )
    )
    target = tmp_path / "must-not-exist.xml"

    result = adapter.write(_write_request(target, FormatId.XML_EET, parsed, foreign.entries))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SOURCE_IDENTITY_CONFLICT"
    assert not target.exists()


def test_agent_eet_tool_preserves_source_snapshot_in_created_slot(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURES / "eet-small.xml", source)

    class Context:
        def __init__(self) -> None:
            self.slots = {}
            self.active_slot = None

        def add_slot(self, key, slot) -> None:
            self.slots[key] = slot

        def activate_slot(self, key) -> None:
            self.active_slot = self.slots[key]

    context = Context()

    result = _tool_parse_eet({"path": str(source)}, context)

    assert result.success
    assert context.active_slot.source_snapshot is not None
    assert context.active_slot.format_id is FormatId.XML_EET
    assert len(context.active_slot.collection) == 1
