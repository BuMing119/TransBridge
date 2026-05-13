"""ParaTranz 导出格式解析器。"""

import json
from pathlib import Path
from .base import FileParser, ParsedDocument


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
            for name in namelist:
                if name.endswith(".json"):
                    content = zf.read(name).decode("utf-8", errors="replace")
                    raw_parts.append(f"--- {name} ---\n{content}")
        return ParsedDocument(
            source_path=path, format="paratranz", title=path.stem,
            raw_text="\n".join(raw_parts),
            metadata={"files": namelist},
        )
