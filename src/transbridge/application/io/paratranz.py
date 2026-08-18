"""Offline ParaTranz JSON FormatAdapter with no client or credential dependency."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from transbridge.application.contracts import (
    Diagnostic,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)

from .catalog import json_format_evidence
from .contracts import (
    CapabilityLevel,
    FormatCapability,
    FormatId,
    FormatProbe,
    ParseRequest,
    ParseResult,
    ParseStats,
    ProbeConfidence,
    ProbeEvidence,
    ProbeEvidenceKind,
    ProbeRequest,
    ProbeStatus,
    SourceSnapshot,
    WriteRequest,
)
from .identity import SourceNamespace
from .paratranz_mapping import (
    map_paratranz_records,
    paratranz_record_from_entry,
)


class ParatranzJsonAdapter:
    format_id = FormatId.JSON_PARATRANZ
    adapter_id = "transbridge.io.paratranz-json"
    adapter_version = "2.0"

    def probe(self, request: ProbeRequest) -> FormatProbe:
        formats = json_format_evidence(request.content)
        if self.format_id not in formats:
            return FormatProbe(ProbeStatus.UNSUPPORTED)
        ordered = tuple(sorted(formats, key=lambda item: item.value))
        status = ProbeStatus.EXACT if len(ordered) == 1 else ProbeStatus.AMBIGUOUS
        return FormatProbe(
            status,
            ordered,
            tuple(
                ProbeEvidence(
                    format_id,
                    ProbeEvidenceKind.SCHEMA,
                    schema,
                    ProbeConfidence.EXACT,
                )
                for format_id, schema in formats.items()
            ),
        )

    def parse(self, request: ParseRequest) -> ParseResult:
        if _cancelled(request.cancellation):
            return self._cancelled_parse(request)
        try:
            content = _local_path(request.source.uri).read_bytes()
        except (OSError, ValueError) as exc:
            return self._failed_parse(
                request,
                (
                    Diagnostic(
                        "PARATRANZ_SOURCE_READ_FAILED",
                        f"Unable to read ParaTranz JSON ({type(exc).__name__}).",
                    ),
                ),
                1,
            )
        if _cancelled(request.cancellation):
            return self._cancelled_parse(request)

        try:
            payload = json.loads(
                content.decode("utf-8-sig"),
                parse_constant=_reject_json_constant,
                parse_float=_parse_finite_json_float,
                object_pairs_hook=_json_object,
            )
        except UnicodeDecodeError as exc:
            return self._failed_parse(
                request,
                (
                    Diagnostic(
                        "PARATRANZ_ENCODING_INVALID",
                        "ParaTranz JSON must be UTF-8.",
                        details=(("byte_offset", exc.start),),
                    ),
                ),
                1,
            )
        except json.JSONDecodeError as exc:
            return self._failed_parse(
                request,
                (
                    Diagnostic(
                        "PARATRANZ_JSON_INVALID",
                        "ParaTranz JSON syntax is invalid.",
                        details=(("line", exc.lineno), ("column", exc.colno), ("offset", exc.pos)),
                    ),
                ),
                1,
            )
        except ValueError:
            return self._failed_parse(
                request,
                (Diagnostic("PARATRANZ_NUMBER_INVALID", "ParaTranz JSON contains a non-finite number."),),
                1,
            )

        snapshot = SourceSnapshot.from_bytes(
            request.source,
            self.format_id,
            content,
            encoding="utf-8-sig",
            bom=b"\xef\xbb\xbf" if content.startswith(b"\xef\xbb\xbf") else b"",
        )
        root_duplicates = tuple(getattr(payload, "duplicate_keys", ()))
        if root_duplicates:
            return self._failed_parse(
                request,
                (
                    Diagnostic(
                        "PARATRANZ_FIELD_CONFLICT",
                        "ParaTranz root object contains duplicate fields.",
                        details=(
                            ("record_index", -1),
                            ("key", None),
                            ("id", None),
                            ("duplicate_fields", root_duplicates),
                        ),
                    ),
                ),
                1,
            )
        records_payload = payload.get("entries") if isinstance(payload, dict) else payload
        options = dict(request.options)
        namespace = request.source_namespace or SourceNamespace.from_fingerprint(
            self.format_id.value,
            _identity_fingerprint(records_payload, fallback=snapshot.sha256),
        )
        external_scope = options.get("external_scope", "offline")
        if not isinstance(external_scope, str) or not external_scope.strip():
            return self._failed_parse(
                request,
                (Diagnostic("PARATRANZ_SCOPE_INVALID", "external_scope must be a non-empty string."),),
                1,
            )
        policy = options.get("invalid_record_policy", "partial")
        if policy not in {"partial", "failed"}:
            return self._failed_parse(
                request,
                (Diagnostic("PARATRANZ_POLICY_INVALID", "invalid_record_policy must be partial or failed."),),
                1,
            )
        batch = map_paratranz_records(records_payload, namespace, external_scope=external_scope)
        if not batch.diagnostics:
            return ParseResult.completed(
                self.format_id,
                request.source,
                snapshot,
                batch.entries,
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                capability=self.capabilities(),
            )

        record_count = len(records_payload) if isinstance(records_payload, list) else 1
        failed_count = len(batch.failed_indices)
        valid_count = len(batch.entries)
        if batch.entries and policy == "partial":
            return ParseResult(
                OperationOutcome.PARTIAL,
                self.format_id,
                request.source,
                snapshot,
                batch.entries,
                batch.diagnostics,
                ParseStats(parsed=valid_count, failed=failed_count),
                self.adapter_id,
                self.adapter_version,
                self.capabilities(),
            )
        return self._failed_parse(
            request,
            batch.diagnostics,
            max(failed_count, 1),
            skipped=max(record_count - failed_count, 0),
        )

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        if request.format_id is not self.format_id:
            return _failed_operation(
                "PARATRANZ_FORMAT_MISMATCH",
                "ParatranzJsonAdapter only writes json.paratranz.",
                run_id=request.context.run_id,
            )
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(
                Diagnostic("PARATRANZ_WRITE_CANCELLED", "ParaTranz JSON write was cancelled."),
                run_id=request.context.run_id,
            )
        _, diagnostics, failed_indices = self._records_for_write(request)
        if diagnostics:
            return OperationResult(
                OperationOutcome.FAILED,
                diagnostics=diagnostics,
                counts=OperationCounts(failed=max(len(failed_indices), 1)),
                run_id=request.context.run_id,
            )
        return OperationResult.completed(
            counts=OperationCounts(succeeded=len(request.entries)),
            run_id=request.context.run_id,
        )

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        validation = self.validate_write(request)
        if validation.outcome is not OperationOutcome.COMPLETED:
            return OperationResult(
                validation.outcome,
                diagnostics=validation.diagnostics,
                counts=validation.counts,
                run_id=validation.run_id,
            )
        records, _, _ = self._records_for_write(request)
        options = dict(request.options)
        if options.get("sort_by_key", False):
            records.sort(key=lambda item: item["key"])
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(
                Diagnostic("PARATRANZ_WRITE_CANCELLED", "ParaTranz JSON write was cancelled."),
                run_id=request.context.run_id,
            )
        try:
            target = _local_path(request.target.uri)
            target.parent.mkdir(parents=True, exist_ok=True)
            if _cancelled(request.cancellation):
                return OperationResult.cancelled(
                    Diagnostic("PARATRANZ_WRITE_CANCELLED", "ParaTranz JSON write was cancelled."),
                    run_id=request.context.run_id,
                )
            target.write_text(
                json.dumps(
                    records,
                    ensure_ascii=bool(options.get("ensure_ascii", False)),
                    indent=_indent_option(options.get("indent", 2)),
                    allow_nan=False,
                ),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            return _failed_operation(
                "PARATRANZ_WRITE_FAILED",
                f"Unable to write ParaTranz JSON ({type(exc).__name__}).",
                run_id=request.context.run_id,
                failed=max(len(request.entries), 1),
            )
        artifact = str(target)
        return OperationResult.completed(
            (artifact,),
            counts=OperationCounts(succeeded=len(records)),
            artifact_refs=(artifact,),
            run_id=request.context.run_id,
        )

    def capabilities(self) -> FormatCapability:
        supported = CapabilityLevel.SUPPORTED
        unavailable = CapabilityLevel.UNAVAILABLE
        return FormatCapability(
            read=supported,
            write=supported,
            round_trip=supported,
            localized=unavailable,
            streaming=unavailable,
            cancel=supported,
            fidelity=supported,
            gui=supported,
            agent=supported,
            mcp=supported,
            publish=unavailable,
        )

    def _records_for_write(
        self,
        request: WriteRequest,
    ) -> tuple[list[dict[str, object]], tuple[Diagnostic, ...], tuple[int, ...]]:
        options = dict(request.options)
        preserve_extensions = options.get("preserve_extensions", True)
        if not isinstance(preserve_extensions, bool):
            return [], (Diagnostic("PARATRANZ_OPTION_INVALID", "preserve_extensions must be boolean."),), (-1,)

        records: dict[int, dict[str, object]] = {}
        diagnostics: list[Diagnostic] = []
        failed: set[int] = set()
        for index, entry in enumerate(request.entries):
            try:
                records[index] = paratranz_record_from_entry(entry, preserve_extensions=preserve_extensions)
            except (TypeError, ValueError) as exc:
                diagnostics.append(
                    Diagnostic(
                        "PARATRANZ_ENTRY_WRITE_INVALID",
                        str(exc),
                        details=(
                            ("record_index", index),
                            ("key", _safe_key(entry)),
                            ("id", _safe_id(entry)),
                        ),
                    )
                )
                failed.add(index)

        key_groups: dict[str, list[int]] = {}
        id_groups: dict[tuple[str, object], list[int]] = {}
        for index, record in records.items():
            key_groups.setdefault(str(record["key"]), []).append(index)
            if "id" in record:
                opaque_id = record["id"]
                id_groups.setdefault((type(opaque_id).__name__, opaque_id), []).append(index)
        for key, indices in key_groups.items():
            if len(indices) > 1:
                for index in indices:
                    failed.add(index)
                    diagnostics.append(
                        Diagnostic(
                            "PARATRANZ_KEY_DUPLICATE",
                            "The ParaTranz key occurs more than once in the write request.",
                            details=(
                                ("record_index", index),
                                ("key", key),
                                ("id", records[index].get("id")),
                                ("conflicting_indices", tuple(indices)),
                            ),
                        )
                    )
        for opaque_key, indices in id_groups.items():
            distinct_keys = {records[index]["key"] for index in indices}
            if len(distinct_keys) > 1:
                for index in indices:
                    failed.add(index)
                    diagnostics.append(
                        Diagnostic(
                            "PARATRANZ_ID_CONFLICT",
                            "The ParaTranz id points to different local keys in the write request.",
                            details=(
                                ("record_index", index),
                                ("key", records[index]["key"]),
                                ("id", records[index].get("id")),
                                ("external_ref", repr(opaque_key)),
                            ),
                        )
                    )
        valid_records = [records[index] for index in sorted(records) if index not in failed]
        return valid_records, tuple(diagnostics), tuple(sorted(failed))

    def _failed_parse(
        self,
        request: ParseRequest,
        diagnostics: tuple[Diagnostic, ...],
        failed: int,
        *,
        skipped: int = 0,
    ) -> ParseResult:
        return ParseResult(
            OperationOutcome.FAILED,
            self.format_id,
            request.source,
            diagnostics=diagnostics,
            stats=ParseStats(failed=failed, skipped=skipped),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )

    def _cancelled_parse(self, request: ParseRequest) -> ParseResult:
        return ParseResult(
            OperationOutcome.CANCELLED,
            self.format_id,
            request.source,
            diagnostics=(Diagnostic("PARATRANZ_PARSE_CANCELLED", "ParaTranz JSON parse was cancelled."),),
            stats=ParseStats(cancelled=1),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )


def _failed_operation(
    code: str,
    message: str,
    *,
    run_id: str | None,
    failed: int = 1,
) -> OperationResult:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic(code, message),),
        counts=OperationCounts(failed=failed),
        run_id=run_id,
    )


def _local_path(uri: str) -> Path:
    if re.match(r"^[A-Za-z]:[\\/]", uri) or uri.startswith(("\\\\", "//")):
        return Path(uri)
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError("ParaTranz offline adapter only accepts local file paths")
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path)
    return Path(uri)


def _cancelled(token: object | None) -> bool:
    if token is None:
        return False
    state = token.is_cancelled
    return bool(state() if callable(state) else state)


def _indent_option(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 16:
        raise ValueError("indent must be null or an integer between 0 and 16")
    return value


def _safe_key(entry: object) -> str | None:
    entry_key = getattr(entry, "entry_key", None) or getattr(entry, "identity", None)
    return entry_key.local_key if hasattr(entry_key, "local_key") else None


def _safe_id(entry: object) -> str | int | None:
    for reference in getattr(entry, "external_refs", ()):
        if getattr(reference, "system", None) == "paratranz":
            return reference.opaque_id
    return None


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is not permitted: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite JSON number is not permitted: {value}")
    return parsed


class _JsonObject(dict):
    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        super().__init__()
        duplicates: list[str] = []
        for key, value in pairs:
            if key in self and key not in duplicates:
                duplicates.append(key)
            self[key] = value
        self.duplicate_keys = tuple(duplicates)


def _json_object(pairs: list[tuple[str, object]]) -> _JsonObject:
    return _JsonObject(pairs)


def _identity_fingerprint(payload: object, *, fallback: str) -> str:
    if not isinstance(payload, list):
        return fallback
    keys = sorted(item.get("key") for item in payload if isinstance(item, dict) and isinstance(item.get("key"), str))
    if not keys:
        return hashlib.sha256(b"json.paratranz:empty").hexdigest()
    material = json.dumps(keys, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
