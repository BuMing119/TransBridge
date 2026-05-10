"""二进制文件解析器：PDF / Word。"""

from pathlib import Path
from .base import FileParser, ParsedDocument


class BinaryFileParser(FileParser):
    supported_extensions = [".pdf", ".docx"]

    def parse(self, path: Path) -> ParsedDocument:
        ext = path.suffix.lower()
        if ext == ".pdf":
            return self._parse_pdf(path)
        else:
            return self._parse_docx(path)

    def _parse_pdf(self, path: Path) -> ParsedDocument:
        try:
            import pdfplumber
        except ImportError:
            raise ImportError("PDF 解析需要 pdfplumber，请执行: pip install pdfplumber")
        sections = []
        raw_parts = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    sections.append({"heading": f"Page {i+1}", "content": text})
                    raw_parts.append(text)
        return ParsedDocument(
            source_path=path, format="pdf", title=path.stem,
            sections=sections, raw_text="\n".join(raw_parts),
            metadata={"pages": len(sections)},
        )

    def _parse_docx(self, path: Path) -> ParsedDocument:
        try:
            from docx import Document
        except ImportError:
            raise ImportError("Word 解析需要 python-docx，请执行: pip install python-docx")
        doc = Document(str(path))
        raw_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                raw_parts.append(para.text)
        raw_text = "\n".join(raw_parts)
        return ParsedDocument(
            source_path=path, format="word", title=path.stem,
            sections=[{"heading": path.stem, "content": raw_text}],
            raw_text=raw_text,
        )
