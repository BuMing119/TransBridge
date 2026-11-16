"""Offline wire contracts from ParaTranz OpenAPI 0.7.0."""

from urllib.parse import urlsplit

import pytest

from transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI

from .test_export_content_suggestions_api import _api, _query


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "suffix", "params", "payload"),
    [
        (
            "list_jobs",
            (7,),
            {"status": 0, "limit": 3, "job_type": "buildTask"},
            "/jobs",
            {"status": ["0"], "limit": ["3"], "type": ["buildTask"]},
            [{"id": 17, "status": 0}],
        ),
        ("list_jobs", (7,), {}, "/jobs", {}, []),
        ("get_job", (7, 17), {}, "/jobs/17", {}, {"id": 17, "status": 2}),
        (
            "get_download_records",
            (7,),
            {"page": 2, "page_size": 6, "begin": "2026-11-12T00:00:00Z", "end": "2026-11-15T00:00:00+08:00"},
            "/artifacts/records",
            {"page": ["2"], "pageSize": ["6"], "begin": ["2026-11-12T00:00:00Z"], "end": ["2026-11-15T00:00:00+08:00"]},
            {"page": 2, "pageSize": 6, "results": []},
        ),
    ],
)
def test_export_jobs_and_download_records(method, args, kwargs, suffix, params, payload):
    api, wire = _api(ParatranzExportAPI, payload)

    assert getattr(api, method)(*args, **kwargs) == payload
    assert wire.requests[0].method == "GET"
    assert urlsplit(wire.requests[0].url).path == "/api/projects/7" + suffix
    assert _query(wire) == params
