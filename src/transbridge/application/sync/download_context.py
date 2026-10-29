"""Apply an explicit transfer target to the installed project context."""


class DownloadContext:
    """Keep a per-operation remote target without changing project binding."""

    def __init__(self, context, values=None):
        if not context.uses_authoritative_projection or context.project_commands is None:
            raise ValueError("请先打开一个本地工程版本。")
        self.original = context
        self.values = dict(values or {})

    def __getattr__(self, name):
        return getattr(self.original, name)

    def resolve_paratranz_target(self):
        raw_id = self.values.get("paratranz_project_id")
        if raw_id in (None, ""):
            return self.original.resolve_paratranz_target()
        try:
            project_id = int(raw_id)
            if isinstance(raw_id, bool) or project_id <= 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("ParaTranz 项目 ID 必须是正整数。") from exc
        return self.original.resolve_paratranz_target(
            explicit_project_id=project_id,
            explicit_project_name=str(self.values.get("paratranz_project_name", "")),
        )
