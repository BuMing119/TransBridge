"""归档解包与打包：7z/zip/rar 三格式统一接口。

选型遵循 ADR-014 决策 4：
- .7z  → py7zr（纯 Python）
- .zip → zipfile（标准库）
- .rar → rarfile + 捆绑 unrar.exe（仅解压，不产 rar）

对上层隐藏格式差异，提供统一 extract()/pack() 接口。
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable


# ── unrar 定位 ───────────────────────────────────────────────

def _find_unrar() -> str:
    """探测 unrar.exe 路径：sys._MEIPASS → 应用目录 → PATH。

    找不到时抛 RuntimeError，调用方应转为友好提示。
    """
    candidates: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(str(Path(meipass) / "unrar.exe"))
    candidates.append(str(Path(__file__).resolve().parent / "bin" / "unrar.exe"))
    candidates.append(str(Path(__file__).resolve().parent / "unrar.exe"))
    which = shutil.which("unrar") or shutil.which("unrar.exe")
    if which:
        candidates.append(which)

    for c in candidates:
        if c and Path(c).exists():
            return c
    raise RuntimeError("未找到 unrar.exe（RAR 解压需要）。请安装 unrar 或确认捆绑二进制已随应用分发。")


def _iter_progress(files: list[str], progress: Callable | None, total: int | None = None):
    """节流进度回调：每处理 50 个文件或每次调用至少发一次。"""
    n = len(files) or 1
    for i, f in enumerate(files, 1):
        if progress and (i % 50 == 0 or i == n):
            progress(i, n)
        yield f


# ── 解包 ─────────────────────────────────────────────────────

def extract(archive_path: str, dest_dir: str, *,
            files: list[str] | None = None,
            progress: Callable | None = None) -> dict:
    """解包归档到 dest_dir。

    files 非 None 时仅提取列表内相对路径（分层提取），跳过 GB 级资源。
    返回 {"dest_dir": ..., "extracted_count": int}。
    """
    archive_path = os.fspath(archive_path)
    dest_dir = os.fspath(dest_dir)
    suffix = Path(archive_path).suffix.lower()
    Path(dest_dir).mkdir(parents=True, exist_ok=True)

    if suffix == ".zip":
        return _extract_zip(archive_path, dest_dir, files, progress)
    if suffix == ".7z":
        return _extract_7z(archive_path, dest_dir, files, progress)
    if suffix == ".rar":
        return _extract_rar(archive_path, dest_dir, files, progress)
    raise ValueError(f"不支持的归档格式: {suffix}（仅支持 .7z/.zip/.rar）")


def _extract_zip(archive_path, dest_dir, files, progress) -> dict:
    try:
        with zipfile.ZipFile(archive_path) as zf:
            members = files if files is not None else zf.namelist()
            total = len(members)
            count = 0
            for i, name in enumerate(members, 1):
                # 跳过目录项
                if name.endswith("/") or name.endswith("\\"):
                    continue
                # 防路径穿越
                target = Path(dest_dir) / name
                if not str(target.resolve()).startswith(str(Path(dest_dir).resolve())):
                    continue
                try:
                    zf.extract(name, dest_dir)
                    count += 1
                except KeyError:
                    continue
                if progress and (i % 50 == 0 or i == total):
                    progress(i, total)
            return {"dest_dir": dest_dir, "extracted_count": count}
    except zipfile.BadZipFile as e:
        raise ValueError(f"归档损坏: {archive_path}（{e}）") from e


def _extract_7z(archive_path, dest_dir, files, progress) -> dict:
    try:
        import py7zr
    except ImportError as e:
        raise RuntimeError("缺少 py7zr 依赖，无法解压 7z 归档") from e
    try:
        with py7zr.SevenZipFile(archive_path, mode="r") as z:
            if files is not None:
                # 仅提取指定成员
                names = [n for n in z.getnames() if n in files]
                z.extract(dest_dir, targets=names)
                return {"dest_dir": dest_dir, "extracted_count": len(names)}
            z.extractall(dest_dir)
            names = z.getnames()
            return {"dest_dir": dest_dir, "extracted_count": len([n for n in names if not n.endswith("/")])}
    except Exception as e:
        raise ValueError(f"7z 解包失败: {archive_path}（{e}）") from e


def _extract_rar(archive_path, dest_dir, files, progress) -> dict:
    try:
        import rarfile
    except ImportError as e:
        raise RuntimeError("缺少 rarfile 依赖，无法解压 rar 归档") from e
    unrar = _find_unrar()
    rarfile.UNRAR_TOOL = unrar
    try:
        with rarfile.RarFile(archive_path) as rf:
            members = files if files is not None else rf.namelist()
            count = 0
            for i, name in enumerate(members, 1):
                if name.endswith("/") or name.endswith("\\"):
                    continue
                rf.extract(name, dest_dir)
                count += 1
                if progress and (i % 50 == 0 or i == len(members)):
                    progress(i, len(members))
            return {"dest_dir": dest_dir, "extracted_count": count}
    except Exception as e:
        raise ValueError(f"rar 解包失败: {archive_path}（{e}）") from e


# ── 打包 ─────────────────────────────────────────────────────

def pack(src_dir: str, archive_path: str, *, fmt: str = "zip",
         progress: Callable | None = None) -> str:
    """将 src_dir 打包为 archive_path（fmt: zip/7z）。默认 zip（zipfile C 实现快）。

    返回 archive_path。不产 rar。
    """
    src_dir = os.fspath(src_dir)
    archive_path = os.fspath(archive_path)
    if fmt == "zip":
        return _pack_zip(src_dir, archive_path, progress)
    if fmt == "7z":
        return _pack_7z(src_dir, archive_path, progress)
    raise ValueError(f"不支持的打包格式: {fmt}（仅支持 zip/7z）")


def _pack_zip(src_dir, archive_path, progress) -> str:
    src = Path(src_dir)
    files = [p for p in src.rglob("*") if p.is_file()]
    total = len(files)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, f in enumerate(files, 1):
            zf.write(f, f.relative_to(src))
            if progress and (i % 50 == 0 or i == total):
                progress(i, total)
    return archive_path


def _pack_7z(src_dir, archive_path, progress) -> str:
    try:
        import py7zr
    except ImportError as e:
        raise RuntimeError("缺少 py7zr 依赖，无法打包 7z 归档") from e
    with py7zr.SevenZipFile(archive_path, mode="w") as z:
        z.writeall(src_dir, arcname="")
    return archive_path