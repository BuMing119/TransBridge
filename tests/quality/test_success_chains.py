"""Release S02: real success chains + cross-entrypoint parity test assets.

Proves each P0 capability has a real fixture / controlled integration success
chain (EET / XT / Strings-SSE / ESP / ParaTranz offline, plus controlled HTTP
post-process and FOMOD typed nine-stage), that parse→write→reparse is
deterministic across three repetitions, that GUI and Agent entrypoints reach
the same composition root with identical results, and that the fixture
checksum registry actually guards against drift.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
from threading import Thread
import uuid
import zipfile

import pytest

from tests.quality.success_chains import (
    FIXTURE_CHECKSUMS,
    FixtureIntegrityError,
    SuccessChain,
    assert_entrypoint_parity,
    chain_sha256,
    run_chain_deterministic,
    summarize,
    verify_fixture_checksums,
)
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.fomod import FOMOD_STAGE_ORDER, FomodRunSpec, PipelineEngine
from transbridge.application.io import (
    EntryKey,
    EntryRevision,
    FormatId,
    ParseRequest,
    SourceDescriptor,
    SourceNamespace,
    StagePolicy,
    TranslationIoUseCase,
    WriteRequest,
)
from transbridge.application.translation import (
    InMemoryPostProcessCheckpointPort,
    OpenAiPostProcessHttpPort,
    PostProcessWorkload,
    TranslationInput,
    build_http_postprocess_stages,
)
from transbridge.entrypoints.agent import parse_translation_source as agent_parse
from transbridge.entrypoints.gui import parse_translation_source as gui_parse
from transbridge.fomod.stages import default_stages

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "contracts" / "io" / "fixtures"
STRINGS_FIXTURES = FIXTURES / "strings"
ESP_FIXTURE = REPOSITORY_ROOT / "tests" / "parser" / "data" / "sample.esp"

USE_CASE = TranslationIoUseCase()


# --------------------------------------------------------------------------- #
# I/O (parse -> modify -> write -> reparse) success chains
# --------------------------------------------------------------------------- #
IO_CHAINS: tuple[dict[str, object], ...] = (
    {
        "chain_id": "eet-parse-write-reparse",
        "fixture": "tests/contracts/io/fixtures/eet-small.xml",
        "format_id": FormatId.XML_EET,
        "translation": "hello, traveler (s02)",
        "outcomes": (OperationOutcome.COMPLETED,),
    },
    {
        "chain_id": "xt-parse-write-reparse",
        "fixture": "tests/contracts/io/fixtures/xt-small.xml",
        "format_id": FormatId.XML_XT,
        "translation": "welcome, traveler (s02)",
        "outcomes": (OperationOutcome.COMPLETED,),
    },
    {
        "chain_id": "strings-parse-write-reparse",
        "fixture": "tests/contracts/io/fixtures/strings/integrity.strings",
        "format_id": FormatId.STRINGS,
        "translation": "hello (s02)",
        "outcomes": (OperationOutcome.COMPLETED, OperationOutcome.PARTIAL),
    },
    {
        "chain_id": "esp-parse-write-reparse",
        "fixture": "tests/parser/data/sample.esp",
        "format_id": FormatId.PLUGIN_SSE,
        "translation": "s02 esp success chain",
        "outcomes": (OperationOutcome.COMPLETED, OperationOutcome.PARTIAL),
    },
    {
        "chain_id": "paratranz-parse-write-reparse",
        "fixture": "tests/contracts/io/fixtures/paratranz_dual_id.json",
        "format_id": FormatId.JSON_PARATRANZ,
        "translation": "s02 paratranz offline chain",
        "outcomes": (OperationOutcome.COMPLETED,),
    },
)


def _ctx(run_id: str) -> RequestContext:
    return RequestContext("quality-s02", run_id=run_id)


def _parse_request(path: Path, format_id: FormatId, run_id: str) -> ParseRequest:
    return ParseRequest(
        SourceDescriptor(str(path), path.name, path.stat().st_size),
        _ctx(run_id),
        format_id,
    )


def _write_request(path: Path, format_id: FormatId, parsed, entries, run_id: str) -> WriteRequest:
    return WriteRequest(
        SourceDescriptor(str(path), path.name),
        format_id,
        tuple(entries),
        1,
        _ctx(run_id),
        source_snapshot=parsed.source_snapshot,
    )


def _first_identity(entry) -> str:
    return str(getattr(entry, "identity", None) or getattr(entry, "entry_key", None))


def _run_io_chain(spec: dict[str, object], workdir: Path) -> dict[str, object]:
    """Run one real parse->modify->write->reparse chain; return a comparable summary."""
    fixture = spec["fixture"]
    fmt = spec["format_id"]
    translation = spec["translation"]
    run_id = f"run-{spec['chain_id']}"

    source = workdir / Path(fixture).name
    shutil.copyfile(REPOSITORY_ROOT / fixture, source)
    parsed = USE_CASE.parse(_parse_request(source, fmt, run_id))
    parse_summary = summarize(parsed)

    result: dict[str, object] = {"parse": parse_summary}
    if parsed.entries:
        changed = replace(parsed.entries[0], translation=translation, stage=1)
        target = workdir / f"out-{Path(fixture).name}"
        written = USE_CASE.write(_write_request(target, fmt, parsed, (changed,), run_id))
        result["write"] = summarize(written)
        if target.exists():
            reparsed = USE_CASE.parse(_parse_request(target, fmt, f"{run_id}-reparse"))
            entries = reparsed.entries
            modified_seen = bool(
                entries
                and translation
                in (getattr(entries[0], "original", ""), getattr(entries[0], "translation", ""))
            )
            result["reparse"] = summarize(reparsed)
            result["semantic"] = {
                "modified_seen": modified_seen,
                "reparsed_count": len(entries),
            }
        else:
            result["reparse"] = {}
            result["semantic"] = {"modified_seen": False, "reparsed_count": 0}
    return result


def _io_chain_runner(spec: dict[str, object], tmp_path: Path):
    def runner():
        workdir = tmp_path / uuid.uuid4().hex
        workdir.mkdir(parents=True)
        return _run_io_chain(spec, workdir)

    return runner


def _io_semantic_assert(spec: dict[str, object]):
    def assert_fn(result: dict[str, object]) -> None:
        parse_summary = result["parse"]
        write_summary = result["write"]
        semantic = result["semantic"]
        assert parse_summary["outcome"] in [item.value for item in spec["outcomes"]]
        assert write_summary.get("outcome") == "completed"
        assert semantic["modified_seen"] is True
        assert semantic["reparsed_count"] >= 1

    return assert_fn


@pytest.mark.parametrize("spec", IO_CHAINS, ids=lambda item: item["chain_id"])
def test_io_real_success_chain_is_deterministic(tmp_path: Path, spec: dict[str, object]) -> None:
    chain = SuccessChain(
        chain_id=spec["chain_id"],
        runner=_io_chain_runner(spec, tmp_path),
        semantic_assert=_io_semantic_assert(spec),
        fixture_path=spec["fixture"],
        checksum=FIXTURE_CHECKSUMS[spec["fixture"]],
        description=f"real {spec['format_id'].value} parse->write->reparse",
    )
    summary = run_chain_deterministic(chain, repetitions=3)

    assert summary["parse"]["entries"] >= 1
    assert summary["write"]["outcome"] == "completed"


# --------------------------------------------------------------------------- #
# Controlled HTTP post-process success chain (refine -> polish -> arbitrate)
# --------------------------------------------------------------------------- #
class PostProcessHandler(BaseHTTPRequestHandler):
    phases: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        user_payload = json.loads(payload["messages"][1]["content"])
        phase = user_payload["phase"]
        self.phases.append(phase)
        values = []
        for entry in user_payload["entries"]:
            current = entry["current"]
            if phase == "refine":
                value = f"refined:{current}"
            elif phase == "polish":
                value = f"polished:{current}"
            else:
                value = "pass"
            values.append({"entry_key": entry["entry_key"], "value": value})
        self._json_response({"results": values})

    def _json_response(self, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


def _http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), PostProcessHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _http_entries() -> tuple[TranslationInput, ...]:
    namespace = SourceNamespace("quality:s02-http")
    return (
        TranslationInput(EntryKey(namespace, "one"), EntryRevision(), "source-one", "draft-one", 2),
        TranslationInput(EntryKey(namespace, "two"), EntryRevision(), "source-two", "draft-two", 2),
    )


def _run_http_postprocess_chain() -> object:
    PostProcessHandler.phases = []
    server, thread = _http_server()
    try:
        port = OpenAiPostProcessHttpPort(timeout_seconds=2)
        base_url = f"http://127.0.0.1:{server.server_port}"
        stages = build_http_postprocess_stages(port, base_url=base_url, model="fixture-model")
        workload = PostProcessWorkload(
            stages,
            stage_policy=StagePolicy(),
            stage_names=("refine", "polish", "arbitrate"),
            checkpoint_port=InMemoryPostProcessCheckpointPort(),
        )
        return workload.run("s02-http-postprocess", _http_entries(), owner_id="owner")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_controlled_http_postprocess_success_chain_deterministic() -> None:
    def assert_fn(result: dict[str, object]) -> None:
        assert result["outcome"] == "completed"
        assert result["accepted_count"] == 2
        assert result["diagnostics"] == []

    chain = SuccessChain(
        chain_id="http-postprocess-refine-polish-arbitrate",
        runner=_run_http_postprocess_chain,
        semantic_assert=assert_fn,
        description="controlled HTTP refine->polish->arbitrate success chain",
    )
    summary = run_chain_deterministic(chain, repetitions=3)

    assert summary["outcome"] == "completed"
    assert summary["accepted_count"] == 2
    assert PostProcessHandler.phases == ["refine", "polish", "arbitrate"]


# --------------------------------------------------------------------------- #
# FOMOD typed nine-stage success chain
# --------------------------------------------------------------------------- #
def _archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("Mod/fomod/ModuleConfig.xml", "<config/>")
        output.writestr("Mod/readme.txt", "hello")


def _make_fomod_run_spec(workdir: Path, run_id: str = "s02-fomod-chain") -> FomodRunSpec:
    archive = workdir / f"{run_id}.zip"
    _archive(archive)
    return FomodRunSpec(
        run_id=run_id,
        new_archive=str(archive),
        new_archive_hash=hashlib.sha256(archive.read_bytes()).hexdigest(),
        output_archive=str(workdir / f"{run_id}-output.zip"),
        target_locale="ja_JP",
        config_hash="config:s02",
        workspace_root=str(workdir / "workspace"),
        ai_enabled=False,
        required_stages=frozenset(FOMOD_STAGE_ORDER),
    )


def test_fomod_typed_nine_stage_success_chain_deterministic(tmp_path: Path) -> None:
    def runner():
        workdir = tmp_path / uuid.uuid4().hex
        workdir.mkdir(parents=True)
        return PipelineEngine(default_stages()).run(_make_fomod_run_spec(workdir))

    def assert_fn(result: dict[str, object]) -> None:
        assert result["outcome"] == "completed"
        assert result["stages"] == [item.value for item in FOMOD_STAGE_ORDER]
        assert any(item["kind"] == "published-archive" for item in result["artifacts"])

    chain = SuccessChain(
        chain_id="fomod-typed-nine-stage",
        runner=runner,
        semantic_assert=assert_fn,
        description="FOMOD typed nine-stage real pipeline success chain",
    )
    summary = run_chain_deterministic(chain, repetitions=3)

    assert summary["outcome"] == "completed"
    assert summary["stages"] == [item.value for item in FOMOD_STAGE_ORDER]
    assert any(item["kind"] == "published-archive" for item in summary["artifacts"])


# --------------------------------------------------------------------------- #
# Cross-entrypoint parity (GUI vs Agent reach the same composition root)
# --------------------------------------------------------------------------- #
def test_gui_and_agent_entrypoints_share_identical_parse_result(tmp_path: Path) -> None:
    source = tmp_path / "parity-eet.xml"
    shutil.copyfile(FIXTURES / "eet-small.xml", source)
    request = _parse_request(source, FormatId.XML_EET, "s02-parity-run")

    parity_chain = SuccessChain(
        chain_id="parity-gui-agent",
        runner=lambda: gui_parse(USE_CASE, request),
        entrypoints=(
            lambda: gui_parse(USE_CASE, request),
            lambda: agent_parse(USE_CASE, request),
        ),
        fixture_path="tests/contracts/io/fixtures/eet-small.xml",
        checksum=FIXTURE_CHECKSUMS["tests/contracts/io/fixtures/eet-small.xml"],
    )

    summary = assert_entrypoint_parity(parity_chain)

    assert summary["outcome"] == "completed"
    assert summary["format_id"] == "xml.eet"
    assert summary["entries"] == 1


def test_parity_harness_requires_at_least_two_entrypoints() -> None:
    chain = SuccessChain(chain_id="single-entrypoint", runner=lambda: None, entrypoints=(lambda: None,))
    with pytest.raises(ValueError, match="at least 2 entrypoints"):
        assert_entrypoint_parity(chain)


# --------------------------------------------------------------------------- #
# Fixture checksum registry guards
# --------------------------------------------------------------------------- #
def test_real_fixture_registry_verifies_clean() -> None:
    verify_fixture_checksums()


def test_tampered_fixture_byte_is_detected(tmp_path: Path) -> None:
    corrupt = tmp_path / "drift.xml"
    corrupt.write_bytes(b"<DocumentElement><ESP>a</ESP></DocumentElement>")
    honest = chain_sha256(corrupt)

    registry = {"tests/contracts/io/fixtures/eet-small.xml": honest}
    with pytest.raises(FixtureIntegrityError):
        verify_fixture_checksums(registry)

    # A chain pointed at a drifted fixture must also fail preflight.
    drifted = SuccessChain(
        chain_id="drift",
        runner=lambda: None,
        fixture_path="tests/contracts/io/fixtures/eet-small.xml",
        checksum="d" * 64,
    )
    with pytest.raises(FixtureIntegrityError):
        drifted.preflight()


# --------------------------------------------------------------------------- #
# Determinism harness is real (negative control)
# --------------------------------------------------------------------------- #
def test_determinism_harness_detects_unstable_chain() -> None:
    state = {"calls": 0}

    def unstable() -> dict[str, object]:
        state["calls"] += 1
        return {"n": state["calls"]}

    chain = SuccessChain(chain_id="unstable", runner=unstable)
    with pytest.raises(AssertionError, match="not deterministic"):
        run_chain_deterministic(chain, repetitions=3)
