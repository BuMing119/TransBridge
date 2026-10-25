"""Offline HTTP state used by the retained ordinary-upload compatibility tests."""

from email.parser import BytesParser
import json
from urllib.parse import parse_qs, urlsplit

import requests

from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.categorized_upload import CategorizedUploadGateway


class ImportSession(requests.Session):
    def __init__(self, *, import_stage=None, lose_response=False):
        super().__init__()
        self.import_stage = import_stage
        self.lose_response = lose_response
        self.files = {}
        self.rows = {}
        self.calls = []
        self.imports = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        url = urlsplit(request.url)
        kind = url.path.split("/projects/7/", 1)[1]
        params = parse_qs(url.query)
        if request.method == "POST" and kind == "files":
            value = self._import(request)
        elif request.method == "GET" and kind == "files":
            value = list(self.files.values())
        elif request.method == "GET" and kind.startswith("files/"):
            value = self.files[int(kind.split("/")[1])]
        elif request.method == "GET" and kind == "strings":
            file_id = int(params["file"][0])
            value = [dict(row) for row in self.rows.values() if row["fileId"] == file_id]
        elif request.method == "GET" and kind.startswith("strings/"):
            value = dict(self.rows[int(kind.split("/")[1])])
        elif request.method == "PUT" and kind.startswith("strings/"):
            row = self.rows[int(kind.split("/")[1])]
            row.update(json.loads(request.body))
            value = dict(row)
        elif request.method == "POST" and kind == "strings":
            value = self._row(json.loads(request.body))
        else:
            raise AssertionError(f"Unexpected offline request: {request.method} {url.path}")
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        return response

    def _import(self, request):
        message = BytesParser().parsebytes(
            f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode() + request.body
        )
        parts = {part.get_param("name", header="content-disposition"): part for part in message.get_payload()}
        upload = parts["file"]
        folder = parts["path"].get_payload(decode=True).decode("utf-8")
        filename = upload.get_filename()
        records = json.loads(upload.get_payload(decode=True))
        file_id = max(self.files, default=0) + 1
        file = {"id": file_id, "name": filename, "folder": folder}
        self.files[file_id] = file
        self.imports.append((folder, filename, records))
        for record in records:
            if self.import_stage is not None:
                record["stage"] = self.import_stage
            self._row({"fileId": file_id, **record})
        if self.lose_response:
            self.lose_response = False
            raise requests.Timeout("Import response lost after committing the file")
        return {"file": file}

    def _row(self, record):
        remote_id = max(self.rows, default=10) + 1
        self.rows[remote_id] = {"id": remote_id, **record}
        return dict(self.rows[remote_id])


def gateway(session):
    config = ParatranzConfig(
        token="test-ordinary-token",
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    return CategorizedUploadGateway(config, session=session)
