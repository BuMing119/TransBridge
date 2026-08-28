"""
ArtifactWorkflow：触发 ParaTranz 导出，轮询完成状态，下载并解压压缩包。

工作流：
  1. 记录当前最新 artifact 的 createdAt 时间 t0
  2. 调用 trigger_export() 触发新导出
  3. 以固定间隔轮询 get_artifacts()，直到出现 createdAt > t0 的新记录（或超时）
  4. 调用 download_artifacts() 将压缩包写入本地
  5. 可选：extract() 解压到目标目录

注意：ParaTranz 目前无独立 Job 状态查询接口，采用轮询 artifacts.createdAt 的降级策略。
"""

from collections.abc import Callable, Mapping
import math
from pathlib import Path
import time
import zipfile

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.publish import ImmediateCommitGuard, PublishCommitGuard
from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceCategory,
    ExternalServiceError,
)
from transbridge.application.sync import ArtifactPublishRequest, ParaTranzArtifactPublisher
from transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI
from transbridge.paratranz.config_manager import ParatranzConfig


class ArtifactWorkflow:
    def __init__(self, config: ParatranzConfig):
        self._api = ParatranzExportAPI(token=config.token, config=config)

    def trigger_and_download(
        self,
        project_id: int,
        save_path: str | Path,
        *,
        poll_interval: float = 3.0,
        timeout: float = 300.0,
        progress_callback: Callable[[str], None] | None = None,
        cancellation: CancellationPort | None = None,
        commit_guard: PublishCommitGuard | None = None,
        run_id: str | None = None,
    ) -> str:
        """
        触发导出，等待完成后下载压缩包到 save_path。

        Args:
            project_id:         ParaTranz 项目 ID
            save_path:          本地保存路径（.zip）
            poll_interval:      轮询间隔（秒），默认 3.0
            timeout:            最长等待时间（秒），默认 300
            progress_callback:  进度回调 (message: str)

        Returns:
            保存文件的路径字符串

        Raises:
            TimeoutError:   超时未出现新导出结果
            RuntimeError:   API 调用失败
        """
        save_path = str(save_path)
        if poll_interval <= 0 or timeout <= 0:
            raise ValueError("poll_interval and timeout must be positive")
        resolved_run_id = run_id or f"legacy-artifact-{project_id}-{time.monotonic_ns()}"
        resolved_guard = commit_guard or ImmediateCommitGuard(resolved_run_id)

        def notify(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        notify("正在触发导出…")
        notify("等待服务端导出完成…")
        publisher = ParaTranzArtifactPublisher(_LegacyArtifactPort(self._api))
        result = publisher.publish(
            ArtifactPublishRequest(
                project_id,
                save_path,
                resolved_run_id,
                resolved_guard,
                cancellation=cancellation,
                poll_interval_seconds=poll_interval,
                max_poll_attempts=max(1, math.ceil(timeout / poll_interval)),
            )
        )
        if result.outcome is OperationOutcome.FAILED and result.diagnostics[0].code == "ARTIFACT_POLL_TIMEOUT":
            raise TimeoutError(f"导出超时（超过 {timeout:.0f} 秒），请稍后到 ParaTranz 网站手动下载。")
        if result.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
            code = result.diagnostics[0].code if result.diagnostics else "ARTIFACT_PUBLISH_FAILED"
            raise RuntimeError(f"ParaTranz artifact publication failed ({code})")
        notify("导出完成，已通过校验并原子发布。")
        notify(f"已下载至：{save_path}")

        return save_path

    def extract(
        self,
        zip_path: str | Path,
        extract_dir: str | Path,
    ) -> list[str]:
        """
        解压导出压缩包。

        Args:
            zip_path:     .zip 文件路径
            extract_dir:  解压目标目录（不存在则自动创建）

        Returns:
            解压出的文件路径列表
        """
        zip_path = Path(zip_path)
        extract_dir = Path(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
            return [str(extract_dir / name) for name in zf.namelist()]


class _LegacyArtifactPort:
    """Normalize the endpoint-shaped legacy API to the atomic publisher port."""

    def __init__(self, api: ParatranzExportAPI) -> None:
        self._api = api

    def get_artifacts(self, project_id: int, *, cancellation: CancellationPort | None = None):
        try:
            if cancellation is None:
                payload = self._api.get_artifacts(project_id)
            else:
                payload = self._api.get_artifacts(project_id, cancellation=cancellation)
        except RuntimeError as exc:
            raise ExternalServiceError(
                ExternalServiceCategory.TRANSPORT,
                "ParaTranz artifact polling failed",
            ) from exc
        if payload is None:
            return ()
        if isinstance(payload, Mapping):
            values = payload.get("results", payload.get("artifacts"))
            if isinstance(values, list) and all(isinstance(item, Mapping) for item in values):
                return tuple(dict(item) for item in values)
            return (dict(payload),)
        if isinstance(payload, list) and all(isinstance(item, Mapping) for item in payload):
            return tuple(dict(item) for item in payload)
        raise ExternalServiceError(
            ExternalServiceCategory.INVALID_RESPONSE,
            "ParaTranz artifact response is invalid",
        )

    def trigger_export(self, project_id: int, *, cancellation: CancellationPort | None = None):
        try:
            if cancellation is None:
                return self._api.trigger_export(project_id)
            return self._api.trigger_export(project_id, cancellation=cancellation)
        except RuntimeError as exc:
            raise ExternalServiceError(
                ExternalServiceCategory.TRANSPORT,
                "ParaTranz export trigger failed",
            ) from exc

    def download_artifact(
        self,
        project_id: int,
        destination: str,
        *,
        cancellation: CancellationPort | None = None,
    ) -> str:
        try:
            if cancellation is None:
                return self._api.download_artifacts(project_id, destination)
            return self._api.download_artifacts(
                project_id,
                destination,
                cancellation=cancellation,
            )
        except RuntimeError as exc:
            raise ExternalServiceError(
                ExternalServiceCategory.TRANSPORT,
                "ParaTranz artifact download failed",
            ) from exc
