# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Project browser and file revision queries shared by the desktop and assistant."""

from collections.abc import Mapping

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError

HISTORY_PAGE_SIZE = 50
HISTORY_LAST_PAGE = 9999


def _records(payload, *keys):
    if isinstance(payload, Mapping):
        payload = next((payload[key] for key in keys if isinstance(payload.get(key), list)), None)
    if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
        raise ExternalServiceError(ExternalServiceCategory.INVALID_RESPONSE, "ParaTranz collection response is invalid")
    return payload


def browse_projects(api, uid, cancellation=None):
    projects = []
    page = 1
    while True:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        response = api.list_projects(page=page, page_size=200, uid=uid, cancellation=cancellation)
        rows = _records(response, "data", "results", "items", "projects")
        projects.extend(rows)
        if len(rows) < 200:
            return projects
        page += 1


def read_file_revision_page(api, project_id, page, cancellation=None):
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= HISTORY_LAST_PAGE:
        raise ValueError(f"page must be an integer from 1 to {HISTORY_LAST_PAGE}")
    response = api.list_file_revisions(project_id, page=page, page_size=HISTORY_PAGE_SIZE, cancellation=cancellation)
    return _records(response, "data", "results", "items", "revisions")
