"""Offline late-batch candidate persistence benchmark against alternate source trees.

Each sample starts from the same large accepted snapshot, then accepts fresh
50-entry batches with real fsync/verification enabled. Setup and imports are
excluded. All entries are synthetic and each owned scratch directory is removed.
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import platform
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


def _positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--samples", type=_positive, default=5)
    parser.add_argument("--accepted", type=_positive, default=10000)
    parser.add_argument("--batch-size", type=_positive, default=50)
    parser.add_argument("--batches", type=_positive, default=10)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    from transbridge.application.io.identity import Provenance
    from transbridge.application.translation import candidate_checkpoint as checkpoint_module
    from transbridge.application.translation.candidate_checkpoint import TranslationCheckpoint
    from transbridge.application.translation.legacy_bridge import LegacyTranslationCandidateSession
    from transbridge.application.translation.models import TranslationAction
    from transbridge.application.translation.workload_models import (
        CandidateTranslation,
        TranslationBatchOutcome,
        TranslationBatchStatus,
        canonical_hash,
    )
    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    assert Path(checkpoint_module.__file__).resolve().is_relative_to(source)
    args.scratch.mkdir(parents=True, exist_ok=True)
    total = args.accepted + args.batch_size * args.batches
    collection = TranslationEntryCollection(
        TranslationEntry(
            id=str(index),
            key=str(index),
            original=f"Synthetic name {index}",
            translation="",
            stage=0,
            context="NPC_:FULL",
        )
        for index in range(total)
    )
    run_id = "candidate-benchmark"
    batch_id = canonical_hash({"seed": args.accepted})
    candidates = tuple(
        CandidateTranslation(
            run_id,
            entry.identity,
            entry.revision,
            TranslationAction.TRANSLATE,
            f"Synthetic translation {entry.id}",
            batch_id,
            1,
            "c" * 64,
            Provenance(run_id, "benchmark", "synthetic"),
        )
        for index, entry in enumerate(collection)
        if index < args.accepted
    )
    seed = TranslationCheckpoint(run_id, "benchmark", "a" * 64, "b" * 64).accept_batch(
        TranslationBatchOutcome(
            batch_id,
            TranslationAction.TRANSLATE,
            TranslationBatchStatus.ACCEPTED,
            tuple(item.entry_key for item in candidates),
            1,
            "SEED_ACCEPTED",
            "Synthetic benchmark seed.",
        ),
        candidates,
    )
    samples = []
    for sample in range(args.samples):
        gc.collect()
        with TemporaryDirectory(prefix="candidate-checkpoint-", dir=args.scratch) as folder:
            port = checkpoint_module.FilesystemTranslationCheckpointPort(folder)
            port.save(seed)
            session = LegacyTranslationCandidateSession(
                run_id=run_id,
                owner_id="benchmark",
                spec_fingerprint="a" * 64,
                input_fingerprint="b" * 64,
                checkpoint=port,
                provider="synthetic",
                model="offline",
            )
            durations = []
            for batch in range(args.batches):
                first = args.accepted + batch * args.batch_size
                translations = {
                    str(index): f"Late translation {index}" for index in range(first, first + args.batch_size)
                }
                started = perf_counter()
                assert session.accept(translations, collection).accepted == args.batch_size
                durations.append((perf_counter() - started) * 1000)
            started = perf_counter()
            restored = checkpoint_module.FilesystemTranslationCheckpointPort(folder).load(run_id)
            recovery_ms = (perf_counter() - started) * 1000
            assert restored.revision == 1 + args.batches and len(restored.candidates) == total
            result = {
                "sample": sample + 1,
                "accept_total_ms": sum(durations),
                "batch_median_ms": statistics.median(durations),
                "batch_max_ms": max(durations),
                "recovery_ms": recovery_ms,
                "stored_bytes": sum(path.stat().st_size for path in Path(folder).iterdir()),
            }
            samples.append(result)
            print(json.dumps(result), flush=True)
    print(
        json.dumps({
            "source": str(source),
            "python": platform.python_version(),
            "accepted": args.accepted,
            "batch_size": args.batch_size,
            "batches": args.batches,
            "samples": args.samples,
            "median": {
                field: statistics.median(row[field] for row in samples) for field in samples[0] if field != "sample"
            },
        }),
        flush=True,
    )


if __name__ == "__main__":
    main()
