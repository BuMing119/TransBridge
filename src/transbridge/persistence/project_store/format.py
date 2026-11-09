# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Identity and diagnostics for the current on-disk format."""

PROJECT_APPLICATION_ID = 0x54425031  # TBP1
PROJECT_FORMAT_VERSION = 1
PROJECT_FORMAT = "transbridge.translation-project"
PROJECT_FILENAME = "project.sqlite3"
PROJECT_ROOT = "project-store-v1"


class ProjectStoreError(RuntimeError):
    pass


class UnsupportedProjectFormat(ProjectStoreError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"不支持的项目格式 / Unsupported project format: {detail}")


class ProjectStorageConflict(ProjectStoreError):
    pass
