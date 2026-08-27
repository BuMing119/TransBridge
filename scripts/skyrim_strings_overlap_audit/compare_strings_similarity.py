"""Compare literal wording overlap between two Skyrim localized STRINGS sets.

Each input may be a directory or a ``.7z`` archive. Files are paired by
``<plugin>_<language>.<strings|dlstrings|ilstrings>`` and records are paired by
their UInt32 string ID. The script intentionally measures literal character
overlap, not semantic similarity: two independent translations of the same
source are expected to be semantically similar.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from typing import Any
import unicodedata

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, LocalizedStringsAdapter, ParseRequest, SourceDescriptor

SUPPORTED_EXTENSIONS = frozenset({".strings", ".dlstrings", ".ilstrings"})
FORMAT_BY_EXTENSION = {
    ".strings": FormatId.STRINGS,
    ".dlstrings": FormatId.DLSTRINGS,
    ".ilstrings": FormatId.ILSTRINGS,
}
DETAIL_FIELDS = (
    "logical_file",
    "string_id",
    "string_id_hex",
    "left_file",
    "right_file",
    "left_text",
    "right_text",
    "left_length",
    "right_length",
    "similarity",
    "category",
    "raw_equal",
    "normalized_equal",
    "evidence_eligible",
)
FILE_FIELDS = (
    "logical_file",
    "left_file",
    "right_file",
    "left_entries",
    "right_entries",
    "common_ids",
    "only_left",
    "only_right",
    "scored",
    "raw_exact",
    "normalized_exact",
    "high_overlap",
    "medium_overlap",
    "low_overlap",
    "mean_similarity",
    "character_weighted_similarity",
)


@dataclass(frozen=True, slots=True)
class StringsSourceFile:
    logical_key: str
    logical_name: str
    path: Path
    display_path: str


def normalize_text(text: str) -> str:
    """Apply conservative display-insensitive normalization for comparison."""
    normalized = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", "", normalized).strip()


def literal_similarity(left: str, right: str) -> float:
    """Return a linear-time multiset character n-gram Dice coefficient."""
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0

    n = min(3, len(left), len(right))
    left_grams = Counter(left[index : index + n] for index in range(len(left) - n + 1))
    right_grams = Counter(right[index : index + n] for index in range(len(right) - n + 1))
    overlap = sum((left_grams & right_grams).values())
    return 2 * overlap / (sum(left_grams.values()) + sum(right_grams.values()))


def _logical_file(path: Path, language: str) -> tuple[str, str] | None:
    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        return None
    stem, separator, file_language = path.stem.rpartition("_")
    if not separator or file_language.casefold() != language.casefold():
        return None
    logical_name = f"{stem}{extension}"
    return logical_name.casefold(), logical_name


def discover_files(root: Path, language: str) -> dict[str, StringsSourceFile]:
    files: dict[str, StringsSourceFile] = {}
    duplicates: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file():
            continue
        logical = _logical_file(path, language)
        if logical is None:
            continue
        logical_key, logical_name = logical
        display_path = path.relative_to(root).as_posix()
        if logical_key in files:
            duplicates.setdefault(logical_key, [files[logical_key].display_path]).append(display_path)
            continue
        files[logical_key] = StringsSourceFile(logical_key, logical_name, path, display_path)

    if duplicates:
        examples = "; ".join(f"{key}: {', '.join(paths)}" for key, paths in list(duplicates.items())[:5])
        raise ValueError(f"Duplicate logical STRINGS files prevent safe pairing: {examples}")
    if not files:
        raise ValueError(f"No STRINGS files with the _{language} language suffix were found under {root}")
    return files


def _validate_archive_members(names: list[str]) -> None:
    unsafe: list[str] = []
    for name in names:
        normalized = name.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
            unsafe.append(name)
    if unsafe:
        raise ValueError(f"The 7z archive contains unsafe paths and was not extracted: {unsafe[:3]}")


@contextmanager
def materialize_input(source: Path) -> Iterator[Path]:
    if source.is_dir():
        yield source
        return
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.casefold() != ".7z":
        raise ValueError(f"Input must be a directory or a .7z archive: {source}")

    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("Reading .7z archives requires py7zr; run the script in the project environment") from exc

    with TemporaryDirectory(prefix="transbridge-strings-compare-") as temporary:
        destination = Path(temporary)
        with py7zr.SevenZipFile(source, mode="r") as archive:
            names = archive.getnames()
            _validate_archive_members(names)
            archive.extractall(path=destination)
        yield destination


def load_entries(source_file: StringsSourceFile) -> dict[int, str]:
    format_id = FORMAT_BY_EXTENSION[source_file.path.suffix.lower()]
    source = SourceDescriptor(
        str(source_file.path),
        source_file.path.name,
        source_file.path.stat().st_size,
        media_type="application/octet-stream",
    )
    result = LocalizedStringsAdapter(format_id).parse(
        ParseRequest(source, RequestContext("strings-literal-similarity"), format_id)
    )
    if result.outcome is not OperationOutcome.COMPLETED:
        messages = "; ".join(item.message for item in result.diagnostics)
        raise ValueError(messages or f"Unable to parse {source_file.display_path}")
    return {int(entry.string_id): entry.original for entry in result.entries}


def _category(
    left_text: str | None,
    right_text: str | None,
    normalized_left: str,
    normalized_right: str,
    score: float | None,
    high_threshold: float,
    medium_threshold: float,
) -> str:
    if left_text is None:
        return "only_right"
    if right_text is None:
        return "only_left"
    if not normalized_left and not normalized_right:
        return "both_empty"
    if not normalized_left:
        return "left_empty"
    if not normalized_right:
        return "right_empty"
    if left_text == right_text:
        return "raw_exact"
    if normalized_left == normalized_right:
        return "normalized_exact"
    if score is not None and score >= high_threshold:
        return "high_overlap"
    if score is not None and score >= medium_threshold:
        return "medium_overlap"
    return "low_overlap"


def compare_file(
    logical_name: str,
    left_file: StringsSourceFile | None,
    right_file: StringsSourceFile | None,
    *,
    high_threshold: float,
    medium_threshold: float,
    min_evidence_length: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    left_entries = load_entries(left_file) if left_file else {}
    right_entries = load_entries(right_file) if right_file else {}
    details: list[dict[str, Any]] = []

    for string_id in sorted(left_entries.keys() | right_entries.keys()):
        left_text = left_entries.get(string_id)
        right_text = right_entries.get(string_id)
        normalized_left = normalize_text(left_text or "")
        normalized_right = normalize_text(right_text or "")
        score = None
        if left_text is not None and right_text is not None and normalized_left and normalized_right:
            score = literal_similarity(normalized_left, normalized_right)
        category = _category(
            left_text,
            right_text,
            normalized_left,
            normalized_right,
            score,
            high_threshold,
            medium_threshold,
        )
        evidence_eligible = (
            score is not None and min(len(normalized_left), len(normalized_right)) >= min_evidence_length
        )
        details.append({
            "logical_file": logical_name,
            "string_id": string_id,
            "string_id_hex": f"0x{string_id:08X}",
            "left_file": left_file.display_path if left_file else "",
            "right_file": right_file.display_path if right_file else "",
            "left_text": left_text if left_text is not None else "",
            "right_text": right_text if right_text is not None else "",
            "left_length": len(normalized_left),
            "right_length": len(normalized_right),
            "similarity": "" if score is None else round(score, 6),
            "category": category,
            "raw_equal": category == "raw_exact",
            "normalized_equal": category in {"raw_exact", "normalized_exact"},
            "evidence_eligible": evidence_eligible,
        })

    file_summary = _summarize_details(details)
    file_summary.update({
        "logical_file": logical_name,
        "left_file": left_file.display_path if left_file else "",
        "right_file": right_file.display_path if right_file else "",
        "left_entries": len(left_entries),
        "right_entries": len(right_entries),
        "common_ids": len(left_entries.keys() & right_entries.keys()),
        "only_left": len(left_entries.keys() - right_entries.keys()),
        "only_right": len(right_entries.keys() - left_entries.keys()),
    })
    return details, file_summary


def _summarize_details(details: list[dict[str, Any]]) -> dict[str, Any]:
    categories = Counter(row["category"] for row in details)
    scored = [row for row in details if row["similarity"] != ""]
    evidence = [row for row in scored if row["evidence_eligible"]]
    evidence_categories = Counter(row["category"] for row in evidence)

    def mean(rows: list[dict[str, Any]]) -> float:
        return sum(float(row["similarity"]) for row in rows) / len(rows) if rows else 0.0

    total_weight = sum(max(int(row["left_length"]), int(row["right_length"])) for row in scored)
    weighted = (
        sum(float(row["similarity"]) * max(int(row["left_length"]), int(row["right_length"])) for row in scored)
        / total_weight
        if total_weight
        else 0.0
    )
    return {
        "scored": len(scored),
        "evidence_scored": len(evidence),
        "evidence_exact": evidence_categories["raw_exact"] + evidence_categories["normalized_exact"],
        "evidence_high_or_exact": (
            evidence_categories["raw_exact"]
            + evidence_categories["normalized_exact"]
            + evidence_categories["high_overlap"]
        ),
        "raw_exact": categories["raw_exact"],
        "normalized_exact": categories["normalized_exact"],
        "high_overlap": categories["high_overlap"],
        "medium_overlap": categories["medium_overlap"],
        "low_overlap": categories["low_overlap"],
        "both_empty": categories["both_empty"],
        "left_empty": categories["left_empty"],
        "right_empty": categories["right_empty"],
        "mean_similarity": round(mean(scored), 6),
        "evidence_mean_similarity": round(mean(evidence), 6),
        "character_weighted_similarity": round(weighted, 6),
        "categories": dict(sorted(categories.items())),
    }


def compare_sources(
    left_root: Path,
    right_root: Path,
    *,
    language: str,
    high_threshold: float,
    medium_threshold: float,
    min_evidence_length: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    left_files = discover_files(left_root, language)
    right_files = discover_files(right_root, language)
    all_details: list[dict[str, Any]] = []
    file_summaries: list[dict[str, Any]] = []

    for logical_key in sorted(left_files.keys() | right_files.keys()):
        left_file = left_files.get(logical_key)
        right_file = right_files.get(logical_key)
        logical_name = (left_file or right_file).logical_name  # type: ignore[union-attr]
        details, file_summary = compare_file(
            logical_name,
            left_file,
            right_file,
            high_threshold=high_threshold,
            medium_threshold=medium_threshold,
            min_evidence_length=min_evidence_length,
        )
        all_details.extend(details)
        file_summaries.append(file_summary)

    summary = _summarize_details(all_details)
    summary.update({
        "left_files": len(left_files),
        "right_files": len(right_files),
        "common_files": len(left_files.keys() & right_files.keys()),
        "only_left_files": len(left_files.keys() - right_files.keys()),
        "only_right_files": len(right_files.keys() - left_files.keys()),
        "total_rows": len(all_details),
        "common_ids": sum(int(item["common_ids"]) for item in file_summaries),
        "only_left": sum(int(item["only_left"]) for item in file_summaries),
        "only_right": sum(int(item["only_right"]) for item in file_summaries),
    })
    return all_details, file_summaries, summary


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _percentage(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.2%}" if denominator else "0.00%"


def write_reports(
    output_dir: Path,
    details: list[dict[str, Any]],
    file_summaries: list[dict[str, Any]],
    summary: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / "details.csv"
    file_path = output_dir / "by_file.csv"
    json_path = output_dir / "summary.json"
    markdown_path = output_dir / "summary.md"
    _write_csv(detail_path, DETAIL_FIELDS, details)
    _write_csv(file_path, FILE_FIELDS, file_summaries)

    payload = {"metadata": metadata, "summary": summary, "files": file_summaries}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    scored = int(summary["scored"])
    exact = int(summary["raw_exact"]) + int(summary["normalized_exact"])
    high_or_exact = exact + int(summary["high_overlap"])
    evidence_scored = int(summary["evidence_scored"])
    markdown = f"""# STRINGS 中文文字重合度报告

- 生成时间：{metadata["generated_at"]}
- 对方：`{metadata["left_input"]}`
- 我的：`{metadata["right_input"]}`
- 语言后缀：`_{metadata["language"]}`
- 匹配文件：{summary["common_files"]}（对方 {summary["left_files"]}，我的 {summary["right_files"]}）
- 匹配 String ID：{summary["common_ids"]}
- 有效双边文本：{scored}
- 原文完全一致：{summary["raw_exact"]}（{_percentage(int(summary["raw_exact"]), scored)}）
- 规范化后新增一致：{summary["normalized_exact"]}（{_percentage(int(summary["normalized_exact"]), scored)}）
- 完全一致合计：{exact}（{_percentage(exact, scored)}）
- 高度重合及以上：{high_or_exact}（{_percentage(high_or_exact, scored)}）
- 平均字面相似度：{float(summary["mean_similarity"]):.2%}
- 按文本长度加权相似度：{float(summary["character_weighted_similarity"]):.2%}
- 达到证据长度的完全一致：{summary["evidence_exact"]}（{_percentage(int(summary["evidence_exact"]), evidence_scored)}）
- 达到证据长度的高度重合及以上：{summary["evidence_high_or_exact"]}（{_percentage(int(summary["evidence_high_or_exact"]), evidence_scored)}）
- 达到证据长度（至少 {metadata["min_evidence_length"]} 字符）的平均相似度：{float(summary["evidence_mean_similarity"]):.2%}

相似度使用规范化文本的字符 n-gram Dice 系数；仅移除空白并执行 Unicode NFKC 规范化，
不使用语义向量。短文本的自然巧合概率较高，因此证据长度统计应优先于短标签统计。
"""
    markdown_path.write_text(markdown, encoding="utf-8")
    return detail_path, file_path, json_path, markdown_path


def _threshold(value: str) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("阈值必须在 0 到 1 之间")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="比较两套 Skyrim STRINGS 汉化的中文字面重合度")
    parser.add_argument("left", type=Path, help="对方汉化目录或 .7z 文件")
    parser.add_argument("right", type=Path, help="我的汉化目录或 .7z 文件")
    parser.add_argument("output_dir", type=Path, help="报告输出目录")
    parser.add_argument("--language", default="chinese", help="只比较此语言后缀，默认 chinese")
    parser.add_argument("--high-threshold", type=_threshold, default=0.85, help="高度重合阈值，默认 0.85")
    parser.add_argument("--medium-threshold", type=_threshold, default=0.60, help="中度重合阈值，默认 0.60")
    parser.add_argument("--min-evidence-length", type=int, default=6, help="证据统计的最短双方文本长度，默认 6")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.medium_threshold > args.high_threshold:
        raise SystemExit("--medium-threshold 不能大于 --high-threshold")
    if args.min_evidence_length < 1:
        raise SystemExit("--min-evidence-length 必须至少为 1")

    with ExitStack() as stack:
        left_root = stack.enter_context(materialize_input(args.left))
        right_root = stack.enter_context(materialize_input(args.right))
        details, file_summaries, summary = compare_sources(
            left_root,
            right_root,
            language=args.language,
            high_threshold=args.high_threshold,
            medium_threshold=args.medium_threshold,
            min_evidence_length=args.min_evidence_length,
        )

    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "left_input": str(args.left.resolve()),
        "right_input": str(args.right.resolve()),
        "language": args.language,
        "metric": "character n-gram multiset Dice coefficient",
        "normalization": "Unicode NFKC plus whitespace removal",
        "high_threshold": args.high_threshold,
        "medium_threshold": args.medium_threshold,
        "min_evidence_length": args.min_evidence_length,
    }
    paths = write_reports(args.output_dir, details, file_summaries, summary, metadata)

    scored = int(summary["scored"])
    exact = int(summary["raw_exact"]) + int(summary["normalized_exact"])
    print(f"完成：匹配 {summary['common_files']} 个文件、{summary['common_ids']} 个 String ID")
    print(f"完全一致：{exact}/{scored} ({_percentage(exact, scored)})")
    print(f"平均字面相似度：{float(summary['mean_similarity']):.2%}")
    print("报告：")
    for path in paths:
        print(f"  {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
