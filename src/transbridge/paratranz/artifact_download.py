"""Generate and download a fresh project export for one batch read."""

from datetime import datetime
from time import sleep

from .api.paratranz_export_api import ParatranzExportAPI


class FreshArtifactDownload:
    def __init__(self, config, *, api_factory=ParatranzExportAPI, poll_interval=3.0, max_polls=100):
        self.config, self.api_factory = config, api_factory
        if poll_interval <= 0 or type(max_polls) is not int or max_polls < 1:
            raise ValueError("导出轮询间隔和次数必须为正数。")
        self.poll_interval, self.max_polls = poll_interval, max_polls

    def download(self, project_id, destination, *, cancellation=None, progress=None):
        def notify(message):
            _check_cancelled(cancellation)
            if progress is not None:
                progress(message)
            _check_cancelled(cancellation)

        api = self.api_factory(config=self.config)
        try:
            notify("正在生成新的压缩包…")
            baseline = api.get_latest_artifact(project_id, cancellation=cancellation)
            if baseline is not None:
                _identity(baseline)
            _check_cancelled(cancellation)
            job = api.trigger_export(project_id, cancellation=cancellation)
            if not isinstance(job, dict) or job.get("status") == -1:
                raise ValueError("ParaTranz 未能启动压缩包生成，请检查项目导出状态。")
            for attempt in range(self.max_polls):
                notify(f"正在等待新压缩包生成（{attempt + 1}/{self.max_polls}）…")
                latest = api.get_latest_artifact(project_id, cancellation=cancellation)
                if latest is not None and _newer(latest, baseline):
                    break
                if attempt + 1 < self.max_polls:
                    if cancellation is None:
                        sleep(self.poll_interval)
                    else:
                        cancellation.wait(self.poll_interval)
                    _check_cancelled(cancellation)
            else:
                raise TimeoutError("等待 ParaTranz 新压缩包超时；未下载旧包，请稍后重试。")
            notify("正在下载新压缩包…")
            api.download_artifacts(project_id, str(destination), cancellation=cancellation)
            _check_cancelled(cancellation)
            after = api.get_latest_artifact(project_id, cancellation=cancellation)
            if after is None or _identity(after) != _identity(latest):
                raise ValueError("下载期间项目导出已变化，本轮未采用压缩包，请重试。")
        finally:
            api.close()


def _identity(artifact):
    if not isinstance(artifact, dict) or type(artifact.get("id")) is not int or artifact["id"] < 1:
        raise ValueError("ParaTranz 导出结果缺少有效身份。")
    timestamp = artifact.get("createdAt")
    try:
        created = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("ParaTranz 导出结果缺少有效生成时间。") from exc
    if created.tzinfo is None:
        raise ValueError("ParaTranz 导出生成时间缺少时区。")
    return artifact["id"], created


def _newer(artifact, baseline):
    identity = _identity(artifact)
    if baseline is None:
        return True
    previous = _identity(baseline)
    return identity[0] != previous[0] and identity[1] > previous[1]


def _check_cancelled(cancellation):
    if cancellation is not None:
        cancellation.raise_if_cancelled()
