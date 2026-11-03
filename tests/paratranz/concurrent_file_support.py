"""Synthetic sessions with independently owned lifetimes and controlled responses."""

from email import policy
from email.parser import BytesParser
import json
from threading import Condition, get_ident
from time import monotonic
from urllib.parse import urlparse

import requests

from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.file_publication import FilePublicationGateway, RequestBudget


class Backend:
    def __init__(self, inventory=None, *, handler=None):
        self.inventory = dict(inventory or {})
        self.rows = {}
        self.handler = handler
        self.condition = Condition()
        self.calls, self.sessions, self.closed = [], [], set()
        self.active = self.peak = 0
        self.per_account, self.peaks = {}, {}
        self.session_active = {}

    def session(self):
        backend = self

        class Session(requests.Session):
            def send(self, request, **kwargs):
                return backend.send(self, request)

            def close(self):
                with backend.condition:
                    backend.closed.add(id(self))
                return super().close()

        session = Session()
        self.sessions.append(session)
        return session

    def send(self, session, request):
        endpoint = urlparse(request.url).path
        kind = (
            "inventory"
            if request.method == "GET" and endpoint.endswith("/files")
            else (
                "download"
                if request.method == "GET"
                else "translation"
                if endpoint.endswith("/translation")
                else "source"
            )
        )
        path = ""
        if request.method == "POST" and request.headers.get("Content-Type", "").startswith("multipart/"):
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.body
            )
            folder = ""
            for part in message.iter_parts():
                if part.get_param("name", header="content-disposition") == "file":
                    path = part.get_filename()
                if part.get_param("name", header="content-disposition") == "path":
                    folder = part.get_payload(decode=True).decode()
            path = folder + path
        account = request.headers.get("Authorization", "")
        row = dict(
            kind=kind,
            endpoint=endpoint,
            path=path,
            request=request,
            account=account,
            thread=get_ident(),
            session=id(session),
            started=monotonic(),
        )
        with self.condition:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.per_account[account] = self.per_account.get(account, 0) + 1
            self.peaks[account] = max(self.peaks.get(account, 0), self.per_account[account])
            self.session_active[id(session)] = self.session_active.get(id(session), 0) + 1
            assert self.session_active[id(session)] == 1, "a requests.Session was concurrently shared"
            self.calls.append(row)
            self.condition.notify_all()
        try:
            value = self.handler(row) if self.handler is not None else None
            if value is None:
                with self.condition:
                    if kind == "inventory":
                        value = [{"id": file_id, "name": name} for name, file_id in self.inventory.items()]
                    elif kind == "download":
                        file_id = int(endpoint.split("/")[-2])
                        value = self.rows.get(
                            file_id,
                            [
                                {
                                    "key": str(file_id),
                                    "original": f"Original {file_id}",
                                    "translation": f"译文 {file_id}",
                                    "stage": 1,
                                    "id": file_id * 100,
                                    "fileId": file_id,
                                }
                            ],
                        )
                    elif kind == "source" and endpoint.endswith("/files"):
                        file_id = max(self.inventory.values(), default=0) + 1
                        self.inventory[path] = file_id
                        row["created_id"] = file_id
                        value = {"file": {"id": file_id}}
                    else:
                        file_id = int(endpoint.split("/")[-2 if kind == "translation" else -1])
                        value = {"file": {"id": file_id}}
            if isinstance(value, Exception):
                raise value
            status, payload, headers = value if isinstance(value, tuple) else (200, value, {})
            response = requests.Response()
            response.status_code = status
            response.url, response.request = request.url, request
            response.headers.update(headers)
            response._content = json.dumps(payload).encode()
            return response
        finally:
            with self.condition:
                row["finished"] = monotonic()
                self.active -= 1
                self.per_account[account] -= 1
                self.session_active[id(session)] -= 1
                self.condition.notify_all()

    def wait_for(self, predicate, timeout=5):
        with self.condition:
            return self.condition.wait_for(lambda: predicate(self.calls), timeout)

    def gateway(self, *, budget=None, cancel=None, user_id=7):
        config = ParatranzConfig(
            token=f"synthetic-account-{user_id}",
            user_id=user_id,
            base_url="https://paratranz.example/api",
            credential_store=UnavailableCredentialStore(),
            environment={},
        )
        return FilePublicationGateway(
            config,
            cancel=cancel,
            budget=budget or RequestBudget(0),
            session=self.session(),
            session_factory=self.session,
        )
