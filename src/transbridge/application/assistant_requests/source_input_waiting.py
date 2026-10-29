"""Recognize the explicit missing-input repair intent in a real tool receipt."""

import json

SOURCE_INPUT_REASON = "source_input_required"
SOURCE_INPUT_CODES = frozenset({"SOURCE_INPUT_REQUIRED", "SOURCE_SNAPSHOT_REQUIRED"})


def needs_source_input(receipt: str) -> bool:
    try:
        payload = json.loads(receipt)
    except (TypeError, ValueError):
        return False
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or result.get("success") is not False:
        return False
    if result.get("error_code") not in SOURCE_INPUT_CODES or result.get("partial"):
        return False
    data = result.get("data")
    repair = data.get("source_input_repair") if isinstance(data, dict) else None
    if (
        not isinstance(repair, dict)
        or repair.get("kind") != "source_input"
        or repair.get("requirement") not in {"originals", "template"}
    ):
        return False
    keys = repair.get("source_keys")
    return isinstance(keys, list) and bool(keys) and all(isinstance(key, str) and key for key in keys)


def waits_only_for_source_input(effects, dispatch) -> bool:
    failed = tuple(effect for effect in effects if effect.status != "succeeded")
    return (
        bool(failed)
        and all(effect.status == "failed" and needs_source_input(effect.receipt) for effect in failed)
        and all(
            receipt.status == "completed"
            or receipt.error_code in SOURCE_INPUT_CODES
            or receipt.status == "cancelled"
            and receipt.error_code == "TOOL_CALL_CANCELLED"
            for receipt in dispatch.step_receipts
        )
    )
