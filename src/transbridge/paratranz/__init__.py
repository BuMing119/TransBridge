"""ParaTranz public API without loading every remote workflow eagerly."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "ParatranzClient": "paratranz_client",
    "ParatranzConfig": "config_manager",
    "ParatranzProjectAPI": "api.paratranz_project_api",
    "ParatranzFilesAPI": "api.paratranz_files_api",
    "ParatranzStringsAPI": "api.paratranz_strings_api",
    "ParatranzTermsAPI": "api.paratranz_terms_api",
    "ParatranzMembersAPI": "api.paratranz_members_api",
    "ParatranzHistoryAPI": "api.paratranz_history_api",
    "ParatranzExportAPI": "api.paratranz_export_api",
    "ParatranzIssuesAPI": "api.paratranz_issues_api",
    "ParatranzScoresAPI": "api.paratranz_contribution_api",
    "ParatranzMailsAPI": "api.paratranz_mails_api",
    "ParatranzUserAPI": "api.paratranz_user_api",
    "ParaTranzUploader": "workflow.uploader",
    "UploadResult": "workflow.uploader",
    "ParaTranzDownloader": "workflow.downloader",
    "DownloadResult": "workflow.downloader",
    "ArtifactWorkflow": "workflow.artifact",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTS))
