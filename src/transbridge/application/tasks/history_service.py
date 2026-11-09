"""Read task history and project availability through independent ports."""

from __future__ import annotations

from collections import defaultdict

from transbridge.application.sync.entry_upload_results import EntryUploadResultStore

from .result_application import ResultApplication
from .storage_contracts import ApplicationState


class TaskHistoryService:
    def __init__(self, task_store, project_commands):
        self.reports = task_store.history()
        self.recovery = task_store.recovery()
        self.uploads = EntryUploadResultStore(task_store.root)
        self.projects = project_commands
        self.applications = ResultApplication.for_commands(self.recovery, project_commands)

    def catalog(self):
        reports = self.reports.list_records()
        runs = (
            *self.recovery.list_summaries(),
            *self.uploads.history(),
            *(
                {"task_id": run_id, "state": "cleanup_pending", "supported": False, "sources": [], "scope_count": 0}
                for run_id in self.recovery.list_cleanup_pending()
            ),
        )
        available = {}
        for record in (*reports, *runs):
            project_id = record.get("project_id")
            if project_id is not None and project_id not in available:
                available[project_id] = self.projects.project_exists(project_id)
            record["target_unavailable"] = project_id is not None and not available[project_id]
        return reports, runs

    def load(self, run_id):
        if self.reports.contains(run_id):
            report = self.reports.load(run_id)
        elif self.uploads.contains(run_id):
            report = _upload_report(self.uploads.load(run_id))
        else:
            report = _candidate_report(self.recovery.load(run_id), self.recovery.load_results(run_id))
        project_id = report.get("project_id")
        report["target_unavailable"] = project_id is not None and not self.projects.project_exists(project_id)
        result_id = report.get("recovery_task_id", run_id)
        if self.recovery.contains(result_id):
            applications = self.applications.list(result_id)
            if applications:
                report["application_label"] = _application_label(applications[-1])
        return report

    def delete(self, run_id):
        if self.uploads.contains(run_id):
            self.uploads.delete_completed(run_id)
            return
        if self.recovery.contains(run_id):
            self.applications.list(run_id)
        self.recovery.delete(run_id, discard_unmerged=True)

    def reclaim_checkpoints(self, run_id):
        self.applications.list(run_id)
        self.recovery.reclaim_checkpoints(run_id)


def _application_label(application):
    state = application.state
    if state is ApplicationState.COMMITTED:
        count = len(application.receipt["entries"])
        return f"项目已保存 {count} 条已确认成果"
    if state is ApplicationState.ACKNOWLEDGEMENT_PENDING and application.receipt is None:
        return "任务应用状态待同步，请刷新核验"
    return {
        ApplicationState.STAGED_UNSAVED: "成果已应用，等待项目保存",
        ApplicationState.ACKNOWLEDGEMENT_PENDING: "项目保存后，任务回执待同步",
        ApplicationState.CONFLICTED: "部分或全部成果存在冲突，待处理",
        ApplicationState.TARGET_UNAVAILABLE: "原项目已删除",
        ApplicationState.DISCARDED: "本次应用已撤销或被后续编辑替换",
        ApplicationState.PREPARED: "已保存成果，等待应用",
    }[state]


def _upload_report(record):
    run_id = record["result_id"]
    project_id, variant_id = record["target"]["project_identity"]
    receipts = {row["index"]: row for row in record["receipts"]}
    return _candidate_report(
        {
            "task_id": run_id,
            "project_id": project_id,
            "variant_id": variant_id,
            "state": record["state"],
            "created_at": record["created_at"],
            "result_kind": "upload",
        },
        {
            "rows": [
                {
                    "entry_key": row["base"]["entry_key"],
                    "before": row["base"],
                    "text": row["payload"]["translation"],
                    "stage": row["payload"]["stage"],
                    "source": "ParaTranz 上传",
                    "decision": "accepted" if receipts.get(index, {}).get("outcome") == "confirmed" else "pending",
                    "report_details": {"note": receipts.get(index, {}).get("error", "")},
                }
                for index, row in enumerate(record["inputs"])
            ]
        },
    )


def _candidate_report(record, payload):
    run_id = record["task_id"]
    sources = defaultdict(list)
    for row in (payload or {}).get("rows", ()):
        before = row.get("before", {})
        sources[row.get("source", record.get("label", "任务成果"))].append({
            "entry_key": row["entry_key"],
            "before_revision": before.get("revision", 0),
            "original": before.get("original", ""),
            "before": before.get("translation", ""),
            "context": before.get("context", "") or "",
            "candidate": row["text"],
            "stage": row["stage"],
            "accepted": row.get("decision") == "accepted",
            "report_details": row.get("report_details", {}),
        })
    for row in (*record.get("entries", ()), *record.get("unmapped", ())):
        key = row.get("entry_key")
        if key is None:
            continue
        sources[record.get("label", "任务成果")].append({
            "entry_key": key,
            "before_revision": row.get("revision", 0),
            "original": row.get("original", ""),
            "before": "",
            "context": row.get("context", "") or "",
            "candidate": row.get("translation", ""),
            "stage": row.get("stage", 0),
            "accepted": False,
            "report_details": {"result_status": "not_applied"},
        })
    return {
        **record,
        "run_id": run_id,
        "sources": [
            {
                "key": source,
                "label": source,
                "snapshot": {
                    "schema": "transbridge.polish-report.v1",
                    "run_id": run_id,
                    "outcome": "completed",
                    "counts": {"input": len(entries), "accepted": sum(row["accepted"] for row in entries)},
                    "entries": entries,
                    "diagnostics": record.get("parse_diagnostics", []),
                },
            }
            for source, entries in sources.items()
        ],
    }
