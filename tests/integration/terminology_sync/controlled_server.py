"""Deterministic localhost ParaTranz terminology server for integration tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import RLock, Thread
from typing import Any
from urllib.parse import parse_qs, urlsplit


class ControlledFaultMode(StrEnum):
    RESPONSE = "response"
    DISCONNECT_BEFORE_COMMIT = "disconnect_before_commit"
    DISCONNECT_AFTER_COMMIT = "disconnect_after_commit"


@dataclass(frozen=True, slots=True)
class ControlledFault:
    method: str
    mode: ControlledFaultMode
    status: int = 503
    body: Mapping[str, Any] | None = None
    retry_after: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", self.method.upper())
        object.__setattr__(self, "mode", ControlledFaultMode(self.mode))
        if not 100 <= self.status <= 599:
            raise ValueError("controlled response status is invalid")


@dataclass(frozen=True, slots=True)
class ControlledRequest:
    sequence: int
    method: str
    path: str
    query: tuple[tuple[str, tuple[str, ...]], ...]
    body: Mapping[str, Any] | None
    response_status: int | None
    committed: bool


class ControlledParaTranzTermsServer:
    """A real HTTP server with deterministic remote state and fault scripts."""

    def __init__(self, *, project_id: int = 123, terms: Iterable[Mapping[str, Any]] = ()) -> None:
        if isinstance(project_id, bool) or project_id < 1:
            raise ValueError("controlled project ID must be positive")
        self.project_id = project_id
        self._lock = RLock()
        self._terms: dict[int, dict[str, Any]] = {}
        self._requests: list[ControlledRequest] = []
        self._faults: deque[ControlledFault] = deque()
        self._revision = 1
        self._next_id = 1
        for term in terms:
            self.seed_term(term)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._thread = Thread(target=self._httpd.serve_forever, name="controlled-paratranz-terms", daemon=True)

    @property
    def api_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/api"

    @property
    def requests(self) -> tuple[ControlledRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    @property
    def write_requests(self) -> tuple[ControlledRequest, ...]:
        return tuple(request for request in self.requests if request.method in {"POST", "PUT", "DELETE"})

    @property
    def terms(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(self._terms[remote_id]) for remote_id in sorted(self._terms))

    def __enter__(self) -> ControlledParaTranzTermsServer:
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def queue_fault(self, fault: ControlledFault) -> None:
        with self._lock:
            self._faults.append(fault)

    def seed_term(self, value: Mapping[str, Any]) -> int:
        with self._lock:
            record = _term_record(value)
            remote_id = record.get("id")
            if remote_id is None:
                remote_id = self._next_id
            if isinstance(remote_id, bool) or not isinstance(remote_id, int) or remote_id < 1:
                raise ValueError("controlled term remote ID must be positive")
            if remote_id in self._terms:
                raise ValueError("controlled term remote ID must be unique")
            self._next_id = max(self._next_id, remote_id + 1)
            record["id"] = remote_id
            record.setdefault("revision", f"term-{self._revision}")
            self._terms[remote_id] = record
            return remote_id

    def mutate_term(self, remote_id: int, **changes: Any) -> None:
        with self._lock:
            if remote_id not in self._terms:
                raise KeyError(remote_id)
            self._revision += 1
            self._terms[remote_id].update(changes)
            self._terms[remote_id]["revision"] = f"term-{self._revision}"

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ControlledParaTranzTerms/1"

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                owner._handle(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PUT(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_DELETE(self) -> None:  # noqa: N802
                owner._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return Handler

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        method = handler.command.upper()
        parsed = urlsplit(handler.path)
        query = {key: tuple(values) for key, values in parse_qs(parsed.query).items()}
        body = _read_json(handler)
        fault = self._take_fault(method)
        if fault is not None and fault.mode is ControlledFaultMode.DISCONNECT_BEFORE_COMMIT:
            self._record(method, parsed.path, query, body, None, False)
            _disconnect(handler)
            return
        if fault is not None and fault.mode is ControlledFaultMode.RESPONSE:
            self._record(method, parsed.path, query, body, fault.status, False)
            headers = {} if fault.retry_after is None else {"Retry-After": str(fault.retry_after)}
            _send_json(handler, fault.status, fault.body or {"message": "controlled failure"}, headers=headers)
            return
        try:
            status, payload, committed = self._dispatch(method, parsed.path, query, body)
        except ValueError as exc:
            self._record(method, parsed.path, query, body, 400, False)
            _send_json(handler, 400, {"message": str(exc)})
            return
        if fault is not None and fault.mode is ControlledFaultMode.DISCONNECT_AFTER_COMMIT:
            self._record(method, parsed.path, query, body, None, committed)
            _disconnect(handler)
            return
        self._record(method, parsed.path, query, body, status, committed)
        headers = {"X-Snapshot-Revision": f"snapshot-{self._revision}"}
        _send_json(handler, status, payload, headers=headers)

    def _dispatch(
        self,
        method: str,
        path: str,
        query: Mapping[str, tuple[str, ...]],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, Any, bool]:
        prefix = f"/api/projects/{self.project_id}/terms"
        if path == prefix and method == "GET":
            page = _query_int(query, "page", 1)
            page_size = _query_int(query, "pageSize", 50)
            with self._lock:
                values = [dict(self._terms[key]) for key in sorted(self._terms)]
                start = (page - 1) * page_size
                selected = values[start : start + page_size]
            total_pages = max((len(values) + page_size - 1) // page_size, 1)
            return (
                200,
                {
                    "terms": selected,
                    "pagination": {"page": page, "totalPages": total_pages, "total": len(values)},
                },
                False,
            )
        if path == prefix and method == "POST":
            if body is None:
                raise ValueError("create body is required")
            with self._lock:
                remote_id = self.seed_term(body)
                self._revision += 1
                self._terms[remote_id]["revision"] = f"term-{self._revision}"
                return 201, dict(self._terms[remote_id]), True
        if path.startswith(prefix + "/"):
            try:
                remote_id = int(path.removeprefix(prefix + "/"))
            except ValueError as exc:
                raise ValueError("remote ID path is invalid") from exc
            with self._lock:
                if remote_id not in self._terms:
                    return 404, {"message": "term not found"}, False
                if method == "PUT":
                    if body is None:
                        raise ValueError("update body is required")
                    self._revision += 1
                    self._terms[remote_id].update(_term_record(body))
                    self._terms[remote_id]["id"] = remote_id
                    self._terms[remote_id]["revision"] = f"term-{self._revision}"
                    return 200, dict(self._terms[remote_id]), True
                if method == "DELETE":
                    del self._terms[remote_id]
                    self._revision += 1
                    return 200, {"id": remote_id, "deleted": True}, True
        return 404, {"message": "controlled route not found"}, False

    def _take_fault(self, method: str) -> ControlledFault | None:
        with self._lock:
            if self._faults and self._faults[0].method == method:
                return self._faults.popleft()
            return None

    def _record(
        self,
        method: str,
        path: str,
        query: Mapping[str, tuple[str, ...]],
        body: Mapping[str, Any] | None,
        response_status: int | None,
        committed: bool,
    ) -> None:
        with self._lock:
            self._requests.append(
                ControlledRequest(
                    len(self._requests) + 1,
                    method,
                    path,
                    tuple(sorted(query.items())),
                    None if body is None else dict(body),
                    response_status,
                    committed,
                )
            )


class NoNetworkSpy:
    """Fail immediately if a default-disabled path touches terminology HTTP."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _unexpected(self, operation: str) -> None:
        self.calls.append(operation)
        raise AssertionError(f"default-disabled flow attempted terminology network operation: {operation}")

    def snapshot_terms(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._unexpected("snapshot_terms")

    def create_term(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._unexpected("create_term")

    def update_term(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._unexpected("update_term")

    def delete_term(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self._unexpected("delete_term")


def _term_record(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = ("term", "translation", "variants", "caseSensitive", "pos", "note", "id", "revision")
    record = {key: value[key] for key in allowed if key in value}
    if not isinstance(record.get("term"), str) or not str(record["term"]).strip():
        raise ValueError("controlled term requires term text")
    if not isinstance(record.get("translation"), str) or not str(record["translation"]).strip():
        raise ValueError("controlled term requires translation text")
    record.setdefault("variants", [])
    record.setdefault("caseSensitive", False)
    record.setdefault("pos", "")
    record.setdefault("note", "")
    return record


def _read_json(handler: BaseHTTPRequestHandler) -> Mapping[str, Any] | None:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return None
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("controlled request JSON must be an object")
    return payload


def _send_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: Any,
    *,
    headers: Mapping[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def _disconnect(handler: BaseHTTPRequestHandler) -> None:
    try:
        handler.connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    handler.connection.close()


def _query_int(query: Mapping[str, tuple[str, ...]], key: str, default: int) -> int:
    values = query.get(key)
    value = default if not values else int(values[0])
    if value < 1:
        raise ValueError(f"{key} must be positive")
    return value


__all__ = [
    "ControlledFault",
    "ControlledFaultMode",
    "ControlledParaTranzTermsServer",
    "ControlledRequest",
    "NoNetworkSpy",
]
