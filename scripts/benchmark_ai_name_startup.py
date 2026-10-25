"""Measure name-authority preparation and deferred mandatory checks offline.

Run with the same Python environment and alternate --source between an isolated
baseline source tree and the current src directory. Names deliberately do not
match the generated terminology, so the samples exercise full lookup cost.
No model, network, user data, or persistent translation state is accessed.
"""

from __future__ import annotations

import argparse
import gc
import inspect
import json
from pathlib import Path
import platform
import re
import statistics
import sys
from time import perf_counter
from types import SimpleNamespace


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "src")
    parser.add_argument("--samples", type=_positive_int, default=5)
    parser.add_argument("--terms", type=_positive_int, default=1000)
    parser.add_argument("--names", type=_positive_int, default=100)
    return parser.parse_args()


def _measure(authority, entries, *, light_scan: bool) -> dict[str, float]:
    start = perf_counter()
    for entry in entries:
        result = authority.for_entry(entry, check_required_terms=False) if light_scan else authority.for_entry(entry)
        assert result.exact_translation is None and not result.blocked and not result.required_terms
    pre = perf_counter() - start
    start = perf_counter()
    for entry in entries:
        result = authority.for_entry(entry)
        assert not result.required_terms
    post = perf_counter() - start
    return {"pre": pre, "post": post, "total": pre + post}


def _summary(samples: list[dict[str, float]]) -> dict[str, dict[str, float]]:
    return {
        phase: {
            "median": statistics.median(row[phase] for row in samples),
            "minimum": min(row[phase] for row in samples),
            "maximum": max(row[phase] for row in samples),
        }
        for phase in ("pre", "post", "total")
    }


def main() -> int:
    args = _arguments()
    source = args.source.resolve()
    # Set before importing any TransBridge module, ahead of editable installs.
    sys.path.insert(0, str(source))
    import transbridge.ai_translator.name_preparation_authority as authority_module
    from transbridge.ai_translator.project_terminology_adapter import (
        EnabledEffectiveTerminologyGate,
        ProjectTerminologyAdapter,
    )
    import transbridge.ai_translator.required_term_matching as matching_module
    from transbridge.ai_translator.term_formats import TermEntry
    from transbridge.ai_translator.term_validation import valid_term_text
    from transbridge.application.terminology.effective import (
        EffectiveSnapshotStatus,
        EffectiveTerminologySnapshot,
        TerminologyLookupContext,
    )
    from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
    from transbridge.application.translation.terminology_run_snapshot import (
        FrozenEffectiveTerminologyPort,
        TerminologyRunSnapshotFactory,
    )

    for module in (authority_module, matching_module):
        assert Path(module.__file__).resolve().is_relative_to(source), module.__file__
    decisions = tuple(
        TermDecision(
            str(index),
            "project",
            "variant",
            f"UniqueTerm{index:05d}",
            f"uniqueterm{index:05d}",
            f"译法{index}",
            scope=TermScope.project(),
            status=DecisionStatus.ADOPTED,
        )
        for index in range(args.terms)
    )
    snapshot = EffectiveTerminologySnapshot(
        "project", "variant", EffectiveSnapshotStatus.READY, "version", "digest", decisions
    )
    frozen = TerminologyRunSnapshotFactory(SimpleNamespace(snapshot=lambda *_: snapshot)).freeze("project", "variant")
    context = TerminologyLookupContext("project", "variant", "A.esp", "version")
    legacy = [TermEntry(item.original, item.translation, "legacy") for item in decisions]
    entries = [SimpleNamespace(original=f"Unrelated Name {index}", context="WEAP:FULL") for index in range(args.names)]
    light_scan = "check_required_terms" in inspect.signature(authority_module.SourceNameAuthority.for_entry).parameters

    class Manager:
        def __init__(self):
            self._effective_loader = ProjectTerminologyAdapter(
                FrozenEffectiveTerminologyPort(frozen), EnabledEffectiveTerminologyGate()
            )

        def lookup_context_for_entry(self, entry):
            return context

        def _effective_terms(self, context):
            return self._effective_loader.load(context, legacy).entries

        def _get_term_matcher_map(self, context):
            return {
                item.term: (item.term, item.translation, item.case_sensitive) for item in self._effective_terms(context)
            }

    samples = []
    for index in range(args.samples):
        gc.collect()
        re.purge()
        valid_term_text.cache_clear()
        authority = authority_module.SourceNameAuthority(Manager(), [], excluded=set())
        samples.append(_measure(authority, entries, light_scan=light_scan))
        print(json.dumps({"sample": index + 1, **samples[-1]}), flush=True)
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "authority_source": authority_module.__file__,
                "matching_source": matching_module.__file__,
                "terms": args.terms,
                "names": args.names,
                "samples": args.samples,
                "light_scan": light_scan,
                "summary": _summary(samples),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
