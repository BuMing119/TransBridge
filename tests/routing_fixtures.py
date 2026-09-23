"""Build routing fixtures through the reducer and repository transaction.

This helper deliberately does not simulate model execution. Model receipt, admission
and cancellation integration tests must call ``run_routing_call`` instead.
"""

from transbridge.application.assistant_requests.journal import EventCause
from transbridge.application.assistant_requests.models import digest
from transbridge.application.assistant_requests.routing_commit import apply_routing_state, direct_replies


def apply_routing_fixture(service, context, batch_id, proposal):
    """Exercise persistence/reducer rules without inventing a model invocation."""
    replies = []

    def apply(state):
        replies.clear()
        batch = apply_routing_state(service, state, batch_id, proposal)
        replies.extend(direct_replies(batch))

    return service.transact(
        context,
        apply,
        append_messages=replies,
        cause=EventCause("routing.applied", "model", {"batch_id": batch_id}, {"proposal_digest": digest(proposal)}),
    )
