"""Inverse commands for the assistant's explicit non-secret configuration fields."""

from transbridge.config.repository import ConfigRepositoryError

FIELDS = frozenset({
    "provider",
    "base_url",
    "model",
    "temperature",
    "max_output_tokens",
    "target_lang",
    "game_profile",
    "term_priority",
    "local_json_path",
    "local_csv_path",
    "local_excel_path",
})


def capture_config_undo(before, after, keys):
    keys = set(keys)
    if not keys or keys - FIELDS or before.path != after.path or after.revision != before.revision + 1:
        raise ConfigRepositoryError(
            "config_undo_invalid", "configuration receipt does not describe one supported commit"
        )
    return {
        "path": before.path,
        "before_revision": before.revision,
        "after_revision": after.revision,
        "before": {key: before.value("llm", key) for key in sorted(keys)},
        "after": {key: after.value("llm", key) for key in sorted(keys)},
    }


def combine_config_undo(receipts):
    if not receipts:
        raise ConfigRepositoryError("config_undo_missing", "configuration receipt is missing")
    result = {**receipts[0], "before": dict(receipts[0]["before"]), "after": dict(receipts[0]["after"])}
    for receipt in receipts[1:]:
        if receipt["path"] != result["path"] or receipt["before_revision"] != result["after_revision"]:
            raise ConfigRepositoryError("config_revision_conflict", "intervening configuration changes prevent undo")
        for key, value in receipt["before"].items():
            if key in result["after"] and result["after"][key] != value:
                raise ConfigRepositoryError("config_undo_invalid", "configuration receipt chain is inconsistent")
            result["before"].setdefault(key, value)
        result["after"].update(receipt["after"])
        result["after_revision"] = receipt["after_revision"]
    return result


def preflight_config_undo(repository, receipt):
    current = repository.load()
    if (
        current.path != receipt["path"]
        or current.revision != receipt["after_revision"]
        or set(receipt["before"]) != set(receipt["after"])
        or set(receipt["before"]) - FIELDS
        or any(current.value("llm", key) != value for key, value in receipt["after"].items())
    ):
        raise ConfigRepositoryError(
            "config_revision_conflict", "configuration changed after this round; no fields restored"
        )
    endpoints = {"provider", "base_url", "model"}
    restoring_endpoints = endpoints.intersection(receipt["before"])
    if restoring_endpoints and (
        restoring_endpoints != endpoints
        or any(
            value is not None and not str(value).strip() for key, value in receipt["before"].items() if key in endpoints
        )
    ):
        raise ConfigRepositoryError(
            "config_undo_endpoint_unsupported",
            "previous endpoint values do not satisfy the current atomic endpoint write contract; no fields restored",
        )


def apply_config_undo(repository, receipt):
    preflight_config_undo(repository, receipt)
    return repository.update_sections({"llm": receipt["before"]}, expected_revision=receipt["after_revision"])
