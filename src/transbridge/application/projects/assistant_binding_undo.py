"""Inverse commands for the local Project's ParaTranz target, never remote data."""

from copy import deepcopy

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult

from .remote_binding import ParaTranzProjectBinding, project_paratranz_binding, project_with_paratranz_binding


def _fail(message):
    raise DomainError(ErrorCategory.CONFLICT, "UNDO_BINDING_CONFLICT", message)


def _binding(project):
    parsed = project_paratranz_binding(project)
    raw = (project.envelope.data.get("remote_bindings") or {}).get("paratranz")
    value = None if parsed is None else parsed.to_dict()
    if raw != value:
        _fail("The binding contains unrecognized fields; this inverse cannot preserve them safely.")
    return value


def _other_fields(project):
    data = deepcopy(project.envelope.data)
    remote = data.get("remote_bindings") or {}
    remote.pop("paratranz", None)
    if not remote:
        data.pop("remote_bindings", None)
    return data


def capture_binding_undo(before, after):
    """Require a single attributed Project commit whose only change is binding."""
    if before.envelope.identity != after.envelope.identity:
        _fail("The active Project changed during binding capture.")
    if _other_fields(before) != _other_fields(after):
        _fail("The command changed Project fields beyond the ParaTranz binding.")
    old, new = _binding(before), _binding(after)
    expected = before.envelope.revision + (old != new)
    if after.envelope.revision != expected:
        _fail("The binding command cannot be attributed to exactly one Project revision.")
    return {
        "schema_version": 1,
        "project_id": before.envelope.identity,
        "before_revision": before.envelope.revision,
        "after_revision": after.envelope.revision,
        "before": old,
        "after": new,
    }


def combine_binding_undo(receipts):
    receipts = tuple(receipts)
    if not receipts:
        raise ValueError("at least one binding receipt is required")
    for receipt in receipts:
        if type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1:
            raise ValueError("unsupported binding receipt schema")
        if any(type(receipt.get(key)) is not int or receipt[key] < 0 for key in ("before_revision", "after_revision")):
            raise ValueError("invalid binding receipt revisions")
        if receipt["after_revision"] < receipt["before_revision"]:
            raise ValueError("binding receipt revisions cannot go backwards")
    first = deepcopy(receipts[0])
    for receipt in receipts[1:]:
        if (
            receipt["project_id"] != first["project_id"]
            or receipt["before_revision"] != first["after_revision"]
            or receipt["before"] != first["after"]
        ):
            _fail("Binding receipts do not form a continuous Project revision chain.")
        first.update(after=deepcopy(receipt["after"]), after_revision=receipt["after_revision"])
    return first


def preflight_binding_undo(lifecycle, receipt):
    receipt = combine_binding_undo((receipt,))
    active = lifecycle.active
    if active is None or active.project.envelope.identity != receipt["project_id"]:
        _fail("Open the original Project before undoing its local binding.")
    current = active.project
    if current.envelope.revision != receipt["after_revision"] or _binding(current) != receipt["after"]:
        _fail("The Project or its binding changed after this command; undo was refused.")
    old = None if receipt["before"] is None else ParaTranzProjectBinding.from_mapping(receipt["before"])
    return project_with_paratranz_binding(current, old, expected_revision=current.envelope.revision)


def apply_binding_undo(lifecycle, receipt, context):
    """CAS-save the local Project binding; no ParaTranz network request is sent."""
    try:
        inverse = preflight_binding_undo(lifecycle, receipt)
        if context.project_id != inverse.envelope.identity:
            _fail("The undo request targets a different Project.")
        if inverse.envelope.revision == receipt["after_revision"]:
            return OperationResult.completed(
                {"project_id": inverse.envelope.identity, "project_revision": inverse.envelope.revision},
                run_id=context.run_id,
            )
        return lifecycle.commit_project_update(inverse, receipt["after_revision"], context)
    except Exception as exc:
        return OperationResult.from_exception(exc, run_id=context.run_id)
