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

import time
import zipfile
from pathlib import Path
from typing import Callable

from src.transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI
from src.transbridge.paratranz.config_manager import ParatranzConfig


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

        def notify(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        # 1. 记录当前 artifact 时间，作为"新导出"的判断基准
        t0: str | None = None
        try:
            latest = self._api.get_artifacts(project_id)
            if latest:
                t0 = latest.get("createdAt")
        except RuntimeError:
            pass  # 项目可能从未导出过，t0 保持 None

        # 2. 触发新导出
        notify("正在触发导出…")
        self._api.trigger_export(project_id)

        # 3. 轮询直到出现新 artifact
        notify("等待服务端导出完成…")
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            try:
                latest = self._api.get_artifacts(project_id)
                new_time = latest.get("createdAt") if latest else None
                if new_time and new_time != t0:
                    notify("导出完成，开始下载…")
                    break
            except RuntimeError:
                continue
        else:
            raise TimeoutError(
                f"导出超时（超过 {timeout:.0f} 秒），"
                "请稍后到 ParaTranz 网站手动下载。"
            )

        # 4. 下载压缩包
        self._api.download_artifacts(project_id, save_path)
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
        zip_path    = Path(zip_path)
        extract_dir = Path(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
            return [str(extract_dir / name) for name in zf.namelist()]
