"""The common statuses every channel's messages and receipts map onto.

Outgoing: draft -> (review) -> queued | held -> sending -> sent -> delivered -> read,
or ending in failed, bounced, blocked or cancelled. Incoming: received.

A receipt never moves a message backwards: a "delivered" that arrives after "read"
is recorded (applied=false) but the message stays "read". Email has only "sent" and
"bounced" (no read tracking, decision L2 A); delivered and read arrive with WhatsApp.
"""

DRAFT, REVIEW, QUEUED, HELD, SENDING = "draft", "review", "queued", "held", "sending"
SENT, DELIVERED, READ = "sent", "delivered", "read"
FAILED, BOUNCED, BLOCKED, CANCELLED, RECEIVED = (
    "failed", "bounced", "blocked", "cancelled", "received")

ALL = (DRAFT, REVIEW, QUEUED, HELD, SENDING, SENT, DELIVERED, READ, FAILED, BOUNCED,
       BLOCKED, CANCELLED, RECEIVED)
# Waiting for the worker (send_after says when).
WAITING = (QUEUED, HELD)
# A person can still change or stop it.
OPEN = (DRAFT, REVIEW, QUEUED, HELD)
# Left Forge (or may have): these count towards the limits.
GONE = (SENDING, SENT, DELIVERED, READ)
ENDED = (FAILED, BOUNCED, BLOCKED, CANCELLED)

_PROGRESS = {QUEUED: 1, HELD: 1, SENDING: 2, SENT: 3, DELIVERED: 4, READ: 5}

LABELS = {
    DRAFT: "Draft", REVIEW: "Waiting for a language review", QUEUED: "Queued",
    HELD: "Held", SENDING: "Sending", SENT: "Sent", DELIVERED: "Delivered", READ: "Read",
    FAILED: "Failed", BOUNCED: "Bounced", BLOCKED: "Stopped by a check",
    CANCELLED: "Cancelled", RECEIVED: "Received",
}


def receipt_applies(current: str, new: str) -> bool:
    """Whether a provider's receipt changes the message's status."""
    if current in ENDED or current in (DRAFT, REVIEW, RECEIVED):
        return False
    if new in (FAILED, BOUNCED):
        return current in (SENDING, SENT)
    if new in _PROGRESS:
        return _PROGRESS[new] > _PROGRESS.get(current, 99)
    return False
