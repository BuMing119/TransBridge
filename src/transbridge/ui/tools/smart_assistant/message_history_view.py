"""Read-only conversation records reconciled with the complete canonical transcript."""


def visible_record(message):
    role = message.get("role")
    if role == "assistant" and (
        message.get("control_batch_id")
        or any(call.get("name") == "submit_request_routing" for call in message.get("tool_calls", ()))
    ):
        return None  # Routing prose is not an accepted user-facing reply.
    content = message.get("content", "")
    if role == "tool":
        content = message.get("display_summary", "")
        role = "system"
    if role not in {"user", "assistant", "system"} or not isinstance(content, str) or not content:
        return None
    if role == "system" and message.get("role") != "tool":
        return None  # Internal prompts are not conversation messages.
    if role == "user":
        from transbridge.application.assistant_requests.attachments import display_user_content

        content = display_user_content(content)
    return {"role": role, "content": content, "message_id": message.get("message_id", "")}


def merge_history(saved, local):
    """Canonical messages win by identity; retain local notices near their next saved turn."""
    identities = {record["message_id"]: index for index, record in enumerate(saved) if record.get("message_id")}
    anonymous_matches = {}
    for index, record in enumerate(saved):
        anonymous_matches.setdefault((record["role"], record["content"]), []).append(index)
    insertions, pending, matched = {}, [], set()
    for record in local:
        identity = record.get("message_id")
        index = identities.get(identity)
        if index is None and not identity:
            index = next(
                (i for i in anonymous_matches.get((record["role"], record["content"]), ()) if i not in matched),
                None,
            )
        if index is None:
            pending.append(record)
        else:
            matched.add(index)
            insertions.setdefault(index, []).extend(pending)
            pending = []
    records = []
    for index, record in enumerate(saved):
        records.extend(insertions.get(index, ()))
        records.append(record)
    records.extend(pending)
    return records


class MessageHistory:
    """Store evicted presentation records; no historical executable QWidget is retained."""

    def __init__(self, button=None, provider=None):
        self.records = []
        self.button = button
        self.provider = provider

    def remember(self, record):
        if record is not None:
            self.records.append(record)
            if self.button is not None:
                self.button.show()

    def snapshot(self, current=()):
        records = self.records + [record for record in current if record is not None]
        if self.provider is not None:
            transcript = self.provider()
            saved, hidden_ids = [], set()
            for message in transcript:
                record = visible_record(message)
                if record is not None:
                    saved.append(record)
                elif message.get("message_id"):
                    hidden_ids.add(message["message_id"])
            records = merge_history(saved, [record for record in records if record.get("message_id") not in hidden_ids])
        return records

    def clear(self):
        self.records.clear()
