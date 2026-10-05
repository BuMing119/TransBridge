"""Offline salvage of legacy proofread logs; never publishes or assumes run identity."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from transbridge.application.io import EntryKey, EntryRevision

from .postprocess import PostProcessCandidate
from .proofread_response import apply_proofread_response


def recover_proofread_logs(directory: Path) -> dict:
    """Keep individually validated responses, including valid subsets of error logs.

    Legacy logs omit the model and version identity. This evidence therefore cannot
    seed an execution checkpoint automatically, and still needs terminology review.
    """
    recovered = {}
    counts = Counter()
    for path in sorted(Path(directory).glob("proofread_call_*.log"), key=_call_number):
        counts["files"] += 1
        text = path.read_text(encoding="utf-8")
        try:
            request_text = text.split("max_tokens=", 1)[1].split("\n", 1)[1].lstrip()
            messages, _ = json.JSONDecoder().raw_decode(request_text)
            systems = " ".join(m.get("content", "") for m in messages if m.get("role") == "system")
            if "You proofread translations" not in systems:
                counts["other_phase"] += 1
                continue
            payload = json.loads(next(m["content"] for m in messages if m.get("role") == "user"))
            items = payload["entries"]
            inputs = tuple(
                PostProcessCandidate(
                    run_id="legacy-log-recovery",
                    entry_key=EntryKey.from_dict(item["entry_key"]),
                    before_revision=EntryRevision(0),
                    original=item["original"],
                    before_text=item["current_translation"],
                    text=item["current_translation"],
                    stage=0,
                    context=item.get("context", ""),
                )
                for item in items
            )
            marker = "[INVALID RESPONSE FROM LLM]" if "[INVALID RESPONSE FROM LLM]" in text else "[RESPONSE FROM LLM]"
            response = text.split(marker, 1)[1]
            response = re.split(r"\n\[(?:REQUEST BUDGET|END CALL|ERROR|EMPTY RESPONSE)\]", response, maxsplit=1)[0]
            parsed = apply_proofread_response(inputs, response.strip(), phase="proofread")
            counts["checked_calls"] += 1
            by_key = {EntryKey.from_dict(item["entry_key"]): item for item in items}
            for candidate in parsed.candidates:
                if not candidate.accepted or "proofread" not in candidate.phases:
                    counts["invalid_entries"] += 1
                    continue
                item = by_key[candidate.entry_key]
                identity = json.dumps(
                    {
                        k: item.get(k)
                        for k in (
                            "entry_key",
                            "original",
                            "current_translation",
                            "context",
                            "terms",
                        )
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                recovered[key] = {
                    "entry_key": candidate.entry_key.to_dict(),
                    "original": candidate.original,
                    "before": candidate.before_text,
                    "candidate": candidate.text,
                    "context": candidate.context,
                    "terms": item.get("terms", {}),
                    "log_file": path.name,
                }
        except (KeyError, IndexError, TypeError, ValueError, StopIteration):
            counts["unreadable_calls"] += 1
    counts["recovered_entries"] = len(recovered)
    counts["changed_entries"] = sum(item["candidate"] != item["before"] for item in recovered.values())
    return {
        "schema": "transbridge.proofread-log-recovery.v1",
        "source": str(Path(directory).resolve()),
        "requires_identity_and_terminology_validation": True,
        "counts": dict(counts),
        "entries": list(recovered.values()),
    }


def _call_number(path: Path) -> int:
    match = re.search(r"_(\d+)\.log$", path.name)
    return int(match[1]) if match else 0
