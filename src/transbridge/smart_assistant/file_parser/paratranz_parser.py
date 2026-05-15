"""ParaTranz 导出格式解析器。"""

import json
import logging
from pathlib import Path
from .base import FileParser, ParsedDocument

logger = logging.getLogger(__name__)

# ZIP bomb 防护限制
MAX_ZIP_SIZE = 100 * 1024 * 1024      # 100 MB 解压后总大小
MAX_ZIP_ENTRIES = 1000                # 最大文件条目数
MAX_SINGLE_ENTRY_SIZE = 50 * 1024 * 1024  # 单个条目最大 50 MB


class ParatranzParser(FileParser):
    supported_extensions = [".json", ".zip"]

    def parse(self, path: Path) -> ParsedDocument:
        ext = path.suffix.lower()
        if ext == ".zip":
            return self._parse_zip(path)
        else:
            return self._parse_json(path)

    def _parse_json(self, path: Path) -> ParsedDocument:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_text = json.dumps(data, ensure_ascii=False, indent=2)
        entries = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict) and "entries" in data:
            entries = data["entries"]
        sections = [{"heading": path.stem, "entries": entries}]
        return ParsedDocument(
            source_path=path, format="paratranz", title=path.stem,
            sections=sections, raw_text=raw_text,
            metadata={"entry_count": len(entries)},
        )

    def _parse_zip(self, path: Path) -> ParsedDocument:
        import zipfile
        raw_parts = []
        namelist = []
        with zipfile.ZipFile(path, "r") as zf:
            namelist = zf.namelist()

            # --- ZIP bomb 防护 ---
            entry_count = len(namelist)
            if entry_count > MAX_ZIP_ENTRIES:
                raise ValueError(
                    f"ZIP 文件条目过多 ({entry_count})，"
                    f"上限为 {MAX_ZIP_ENTRIES}。文件可能为恶意压缩炸弹。"
                )

            total_uncompressed = sum(
                info.file_size for info in zf.infolist()
            )
            if total_uncompressed > MAX_ZIP_SIZE:
                raise ValueError(
                    f"ZIP 文件解压后总大小 ({total_uncompressed / 1024 / 1024:.1f} MB) 超过上限 "
                    f"({MAX_ZIP_SIZE / 1024 / 1024:.0f} MB)。文件可能为恶意压缩炸弹。"
                )

            for info in zf.infolist():
                if info.file_size > MAX_SINGLE_ENTRY_SIZE:
                    raise ValueError(
                        f"ZIP 条目 '{info.filename}' 解压后大小 "
                        f"({info.file_size / 1024 / 1024:.1f} MB) 超过上限 "
                        f"({MAX_SINGLE_ENTRY_SIZE / 1024 / 1024:.0f} MB)。"
                        f"文件可能为恶意压缩炸弹。"
                    )
            # --- 防护结束 ---

            for name in namelist:
                if name.endswith(".json"):
                    content = zf.read(name).decode("utf-8", errors="replace")
                    raw_parts.append(f"--- {name} ---\n{content}")

        return ParsedDocument(
            source_path=path, format="paratranz", title=path.stem,
            raw_text="\n".join(raw_parts),
            metadata={"files": namelist},
        )
