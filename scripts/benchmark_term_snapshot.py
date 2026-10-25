"""Measure warm batch terminology lookups against an isolated source tree.

Run the same arguments with baseline/current --source directories. Each sample
uses a fresh synthetic pool, warms each lookup once, then measures repeated
snapshot, exact-match and request-finalization operations. No LLM, embedding,
network or user translation data is used. Temporary data stays under --scratch.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import platform
import statistics
import sys
from tempfile import TemporaryDirectory
from threading import RLock
from time import perf_counter
from types import SimpleNamespace


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True, help="Directory for task-owned temporary data")
    parser.add_argument("--terms", type=_positive_int, default=10000)
    parser.add_argument("--batches", type=_positive_int, default=10)
    parser.add_argument("--samples", type=_positive_int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    # Baseline has an unrelated package import cycle when validation imports first.
    importlib.import_module("transbridge.application.translation")
    import transbridge.ai_translator.shared_term_pool as pool_module
    from transbridge.ai_translator.term_database import TermDatabaseManager
    from transbridge.ai_translator.translation_request import translation_request
    from transbridge.config.llm import LLMConfig

    for module_name in (
        "transbridge.ai_translator.shared_term_pool",
        "transbridge.ai_translator.term_database",
        "transbridge.ai_translator.term_lookup_cache",
        "transbridge.ai_translator.translation_request",
    ):
        module = importlib.import_module(module_name)
        assert Path(module.__file__).resolve().is_relative_to(source), module.__file__
    rows = []
    temporary_root = args.scratch.resolve()
    temporary_root.mkdir(parents=True, exist_ok=True)
    for _ in range(args.samples):
        with TemporaryDirectory(dir=temporary_root) as temporary:
            root = Path(temporary)

            def ai_dir(stem):
                path = root / "legacy" / stem
                path.mkdir(parents=True, exist_ok=True)
                return str(path)

            LLMConfig.get_ai_translator_dir = staticmethod(ai_dir)
            config = LLMConfig(term_priority=[], enable_semantic_match=False)
            pool = pool_module.SharedTermPool("synthetic", "variant", config.target_lang, path=root / "terms.json")
            pool.publish([(f"Term{index:05}", f"译文{index}", "synthetic", "") for index in range(args.terms)])
            manager = TermDatabaseManager(config, "source.esp", shared_term_pool=pool)
            manager.load_all()
            entries = [
                SimpleNamespace(key=f"entry-{index}", original=f"Term{index:05}", terminology_plugin_id="source.esp")
                for index in range(min(args.terms, 20))
            ]
            context = manager.lookup_context_for_entry(entries[0])
            originals = [entry.original for entry in entries]

            def build(_entries, _flat, _kind, *, terms_by_entry):
                assert all(terms_by_entry[row.key].get(row.original) for row in entries)
                return []

            def finalize():
                request = translation_request(
                    manager=manager,
                    builder=SimpleNamespace(build_translation_prompt=build),
                    entries=entries,
                    batch_type="dialogue",
                    config=config,
                    shared_terms={},
                    terms_lock=RLock(),
                    required_terms={},
                    effective_required={},
                    log=lambda _: None,
                )
                request.finalize(SimpleNamespace(terms_by_entry={}))

            actions = {
                "snapshot": lambda: pool.snapshot("source.esp"),
                "exact": lambda: manager.exact_match(originals, context=context),
                "batch_finalize": finalize,
            }
            sample = {}
            for name, action in actions.items():
                action()
                start = perf_counter()
                for _ in range(args.batches):
                    action()
                sample[name] = (perf_counter() - start) * 1000 / args.batches
            rows.append(sample)
    result = {
        "source": str(source),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "terms": args.terms,
        "batches_per_sample": args.batches,
        "samples": args.samples,
        "entries_per_batch": len(entries),
        "unit": "milliseconds_per_batch",
        "measurements": {
            name: {
                "median": statistics.median(row[name] for row in rows),
                "min": min(row[name] for row in rows),
                "max": max(row[name] for row in rows),
            }
            for name in actions
        },
        "raw_samples": rows,
    }
    payload = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
