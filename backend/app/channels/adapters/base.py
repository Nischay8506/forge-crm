"""What every email adapter takes and returns."""

import uuid
from dataclasses import dataclass, field
from typing import Protocol

SENT, RETRY, FAILED, BOUNCED, UNKNOWN = "sent", "retry", "failed", "bounced", "unknown"


@dataclass(frozen=True)
class OutgoingEmail:
    message_id: uuid.UUID
    to: str
    from_address: str
    from_name: str | None
    reply_to: str | None
    subject: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # keep addresses and text out of logs
        return f"<OutgoingEmail {self.message_id}>"


@dataclass(frozen=True)
class SendResult:
    """sent: the provider accepted it. retry: it certainly did not go (e.g. the server
    could not be reached), so trying again is safe. failed or bounced: it did not go
    and trying again will not help. unknown: the connection dropped while sending, so
    nobody can tell whether it went; never tried again automatically.
    `reason` is for people and never contains an address."""

    outcome: str
    provider_ref: str | None = None
    reason: str | None = None
    account_problem: bool = False


class EmailAdapter(Protocol):
    name: str

    async def send(self, mail: OutgoingEmail) -> SendResult: ...

    async def check(self) -> tuple[bool, str]: ...
