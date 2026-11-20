"""Historical dialogue bytes remain usable through project and publish workflows."""

from dataclasses import replace
import struct
from uuid import uuid4

import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from tests.integration.publish.test_plugin_fidelity import _publish
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, WriteRequest
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.config.text_encoding import DEFAULT_READ_POLICY, STRICT_READ_POLICY, XTRANSLATOR_READ_POLICY
from transbridge.parser.plugin_parser import PluginParser


@pytest.fixture(params=[(b"\x8d", "Ť", "\x8d"), (b"\x81", "Ѓ", "\x81"), (b"\x81\x98", "Ѓ\ufffd", "\x81˜")])
def compatibility_source(tmp_path, request):
    payload, historical, windows = request.param
    prefix = b"A" * 89
    info = record(
        "INFO",
        0x0529C112,
        field("ZZZZ", b"opaque") * 5
        + field("TRDT", bytes(12) + b"\x01" + bytes(11))
        + field("NAM1", prefix + payload + b"\0"),
    )
    children = struct.pack("<4sIIiHHHH", b"GRUP", len(info) + 24, 0x10, 7, 0, 0, 0, 0) + info
    original = plugin_bytes({
        "QUST": record("QUST", 1, field("EDID", b"Quest\0")),
        "DIAL": record("DIAL", 0x10, reference("QNAM", 1) + field("FULL", b"Topic\0")) + children,
    })
    path = tmp_path / "3DNPC.esp"
    path.write_bytes(original)
    return path, original, prefix.decode() + historical, prefix.decode() + windows


@pytest.mark.parametrize("policy", [XTRANSLATOR_READ_POLICY, DEFAULT_READ_POLICY])
def test_info_text_publishes_with_its_saved_policy_and_strict_utf8_validation(
    tmp_path, caplog, compatibility_source, policy
):
    source, original, historical, windows = compatibility_source
    expected = historical if policy == XTRANSLATOR_READ_POLICY else windows
    context = RequestContext("compatibility", run_id="compatibility")
    parsed = SsePluginAdapter().parse(
        ParseRequest(
            SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE, options=(("text_read_policy", policy),)
        )
    )

    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    assert [entry.original for entry in parsed.entries] == ["Topic", expected]
    if policy == XTRANSLATOR_READ_POLICY:
        assert "3DNPC.esp INFO NAM1 (0529C112, subrecord 6)" in caplog.text
        assert "byte 89" in caplog.text
    else:
        assert "historical" not in caplog.text
    assert any(diagnostic.code == "GAME_TEXT_FALLBACK_USED" for diagnostic in parsed.diagnostics)
    assert dict(parsed.source_snapshot.metadata)["text_read_policy"] == policy
    target = tmp_path / "Translated.esp"
    written = _publish(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.PLUGIN_SSE,
            (replace(parsed.entries[0], translation="话题", stage=1),),
            1,
            context,
            source_snapshot=parsed.source_snapshot,
        )
    )

    assert written.outcome is OperationOutcome.COMPLETED, written.message
    assert written.validation.fidelity_valid
    reread = PluginParser().parse_plugin(target, text_read_policy=STRICT_READ_POLICY, raise_on_error=True)
    assert [entry.original for entry in reread] == ["话题", expected]
    assert source.read_bytes() == original


def test_historical_info_text_creates_saves_and_reopens_project(tmp_path, compatibility_source):
    source, original, _historical, expected = compatibility_source
    root = tmp_path / "project-data"
    context = RequestContext("compatibility", run_id="compatibility")

    def services():
        return build_persistence_v2_services(root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")

    current = services()
    try:
        prepared = current.gui_project_commands.prepare_create(
            ProjectProvisioningRequest("3DNPC", source=ProjectSourceRequest(str(source))), context
        )
        assert prepared.is_success and prepared.value is not None, prepared.diagnostics
        committed = current.gui_project_commands.commit_create(prepared.value["token"], context)
        assert committed.is_success, committed.diagnostics
        hydration = current.gui_project_commands.consume_create_hydration(prepared.value["project_id"], context)
        assert hydration.is_success, hydration.diagnostics
        assert [entry.original for entry in hydration.value.sources[0].entries] == ["Topic", expected]
        assert dict(hydration.value.sources[0].source_snapshot.metadata)["text_read_policy"] == DEFAULT_READ_POLICY
        entries = current.project_lifecycle.active.variant.snapshot().entries
        assert current.gui_project_commands.save(context).is_success
    finally:
        current.close()

    restarted = services()
    try:
        opened = restarted.current_project_opener.prepare_active(context)
        assert opened.is_success and opened.value is not None, opened.diagnostics
        assert restarted.current_project_opener.activate(opened.value, context).is_success
        assert restarted.project_lifecycle.active.variant.snapshot().entries == entries
        assert source.read_bytes() == original
    finally:
        restarted.close()
