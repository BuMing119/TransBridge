"""Provide complete baselines only to operations that explicitly need them."""

from transbridge.application.contracts import DomainError
from transbridge.persistence.v2.baselines import BaselineRegistry
from transbridge.persistence.v2.ids import ProjectId

from .connection import project_connection
from .records import read_baselines


class ProjectBaselineRegistry(BaselineRegistry):
    def __init__(self, projects) -> None:
        super().__init__()
        self._projects = projects

    def provide(self, project, variant_ref, context):
        try:
            return super().provide(project, variant_ref, context)
        except DomainError as exc:
            if exc.code != "LEGACY_SOURCE_BASELINE_REQUIRED":
                raise
        path = self._projects.store_for(ProjectId(project.envelope.identity)).path
        with project_connection(path) as connection:
            return read_baselines(connection)
