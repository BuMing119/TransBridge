"""English CLI and report renderer for the Skyrim STRINGS overlap audit."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

try:
    from .compare_strings_similarity import (
        DETAIL_FIELDS,
        FILE_FIELDS,
        _percentage,
        _write_csv,
        compare_sources,
        materialize_input,
    )
except ImportError:  # Direct execution from this directory.
    from compare_strings_similarity import (  # type: ignore[no-redef]
        DETAIL_FIELDS,
        FILE_FIELDS,
        _percentage,
        _write_csv,
        compare_sources,
        materialize_input,
    )


def write_reports_english(
    output_dir: Path,
    details: list[dict[str, Any]],
    file_summaries: list[dict[str, Any]],
    summary: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[Path, Path, Path, Path]:
    """Write English Markdown plus language-neutral CSV and JSON evidence."""
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
    left_source = _markdown_source(str(metadata["left_input"]))
    right_source = _markdown_source(str(metadata["right_input"]))
    markdown = f"""# Skyrim STRINGS Literal Text Overlap Report

- Generated at: {metadata["generated_at"]}
- Left/reference source: {left_source}
- Right/compared source: {right_source}
- Selected language suffix: `_{metadata["language"]}`
- Matched files: {summary["common_files"]} (left {summary["left_files"]}, right {summary["right_files"]})
- Matched String IDs: {summary["common_ids"]}
- Non-empty texts present on both sides: {scored}
- Byte-for-text exact matches: {summary["raw_exact"]} ({_percentage(int(summary["raw_exact"]), scored)})
- Additional matches after normalization: {summary["normalized_exact"]} ({_percentage(int(summary["normalized_exact"]), scored)})
- Exact after normalization total: {exact} ({_percentage(exact, scored)})
- High overlap or exact: {high_or_exact} ({_percentage(high_or_exact, scored)})
- Mean literal similarity: {float(summary["mean_similarity"]):.2%}
- Character-length-weighted similarity: {float(summary["character_weighted_similarity"]):.2%}
- Evidence-length exact matches: {summary["evidence_exact"]} ({_percentage(int(summary["evidence_exact"]), evidence_scored)})
- Evidence-length high overlap or exact: {summary["evidence_high_or_exact"]} ({_percentage(int(summary["evidence_high_or_exact"]), evidence_scored)})
- Mean similarity where both texts contain at least {metadata["min_evidence_length"]} characters: {float(summary["evidence_mean_similarity"]):.2%}

Similarity is the multiset character n-gram Dice coefficient over normalized text. Normalization applies Unicode
NFKC and removes whitespace; it does not use semantic embeddings. Short labels are more likely to match naturally,
so evidence-length and long-text results should be given more weight than the unfiltered exact-match percentage.
"""
    markdown_path.write_text(markdown, encoding="utf-8")
    return detail_path, file_path, json_path, markdown_path


def _markdown_source(value: str) -> str:
    """Render public web sources as links and local inputs as code."""
    if value.startswith(("https://", "http://")):
        return f"<{value}>"
    return f"`{value}`"


def _threshold(value: str) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("threshold must be between 0 and 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare literal wording overlap between two Skyrim STRINGS sets")
    parser.add_argument("left", type=Path, help="left/reference directory or .7z archive")
    parser.add_argument("right", type=Path, help="right/compared directory or .7z archive")
    parser.add_argument("output_dir", type=Path, help="report output directory")
    parser.add_argument(
        "--left-report-source",
        help="public URL or label to record for the left input instead of its local path",
    )
    parser.add_argument(
        "--right-report-source",
        help="public URL or label to record for the right input instead of its local path",
    )
    parser.add_argument("--language", default="chinese", help="filename language suffix; default: chinese")
    parser.add_argument("--high-threshold", type=_threshold, default=0.85, help="high-overlap threshold; default: 0.85")
    parser.add_argument(
        "--medium-threshold", type=_threshold, default=0.60, help="medium-overlap threshold; default: 0.60"
    )
    parser.add_argument(
        "--min-evidence-length",
        type=int,
        default=6,
        help="minimum length on both sides for evidence-length statistics; default: 6",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.medium_threshold > args.high_threshold:
        raise SystemExit("--medium-threshold cannot exceed --high-threshold")
    if args.min_evidence_length < 1:
        raise SystemExit("--min-evidence-length must be at least 1")

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
        "left_input": args.left_report_source or str(args.left.resolve()),
        "right_input": args.right_report_source or str(args.right.resolve()),
        "language": args.language,
        "metric": "character n-gram multiset Dice coefficient",
        "normalization": "Unicode NFKC plus whitespace removal",
        "report_language": "English",
        "high_threshold": args.high_threshold,
        "medium_threshold": args.medium_threshold,
        "min_evidence_length": args.min_evidence_length,
    }
    paths = write_reports_english(args.output_dir, details, file_summaries, summary, metadata)

    scored = int(summary["scored"])
    exact = int(summary["raw_exact"]) + int(summary["normalized_exact"])
    print(f"Completed: matched {summary['common_files']} files and {summary['common_ids']} String IDs")
    print(f"Exact after normalization: {exact}/{scored} ({_percentage(exact, scored)})")
    print(f"Mean literal similarity: {float(summary['mean_similarity']):.2%}")
    print("Reports:")
    for path in paths:
        print(f"  {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
