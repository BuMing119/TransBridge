"""Measure stream parsing and repetition CPU separately, without logs or Qt.

Use the same --source snapshots as benchmark_ai_stream_concurrency.py. The
fallbacks exercise the pre-incremental implementation when benchmarking an old
snapshot. This script contains no copied production parsing/detection rules.
"""

import argparse
import importlib
import json
from pathlib import Path
import statistics
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--rows", type=int, default=50)
    parser.add_argument("--chunk", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.rows, args.chunk, args.iterations, args.samples) < 1:
        parser.error("all sizes must be positive")
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    prompts = importlib.import_module("transbridge.ai_translator.prompt_builder")
    translator = importlib.import_module("transbridge.ai_translator.translator")
    for module in (prompts, translator):
        if not Path(module.__file__).resolve().is_relative_to(source):
            raise RuntimeError(f"Import escaped --source: {module.__file__}")
    payload = json.dumps(
        {
            "results": [
                {
                    "entry_id": f"e{i:06d}",
                    "translation": f"记录{i}：公会守卫请你沿河前往城镇，找到失踪的信使，将密封信件交给商人。",
                }
                for i in range(args.rows)
            ]
        },
        ensure_ascii=False,
    )
    chunks = [payload[i : i + args.chunk] for i in range(0, len(payload), args.chunk)]

    def parse():
        if hasattr(translator, "TranslationStream"):
            stream = translator.TranslationStream()
            for chunk in chunks:
                stream.feed(chunk)
            assert len(stream.translations) == args.rows
        else:
            text = ""
            for chunk in chunks:
                text += chunk
                result = prompts._extract_partial_translation_results(text)
            assert len(result) == args.rows

    def detect():
        if hasattr(translator, "StreamRepetitionDetector"):
            detector = translator.StreamRepetitionDetector()
            for chunk in chunks:
                assert detector.feed(chunk) == (False, -1)
        else:
            text = ""
            for chunk in chunks:
                text += chunk
                assert translator.AutoTranslator._detect_stream_repetition(text) == (False, -1)

    result = {
        "source": str(source),
        "rows": args.rows,
        "chars": len(payload),
        "chunk": args.chunk,
        "minimum_iterations": args.iterations,
        "samples": {},
        "iterations": {},
    }
    for name, operation in (("parse_cpu_ms", parse), ("repetition_cpu_ms", detect)):
        operation()
        samples = []
        iterations = []
        for _ in range(args.samples):
            started = time.process_time()
            count = 0
            # Windows process CPU clocks can have ~15ms resolution. Accumulate
            # enough work to avoid reporting short callbacks as zero CPU.
            while count < args.iterations or time.process_time() - started < 0.25:
                operation()
                count += 1
            samples.append((time.process_time() - started) * 1000 / count)
            iterations.append(count)
        result["samples"][name] = samples
        result["iterations"][name] = iterations
        result[name] = statistics.median(samples)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
