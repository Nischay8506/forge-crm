"""What every mail source gives back, and the mailbox's stored secret."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core import secrets
from app.models import Mailbox


class MailError(Exception):
    """Reading failed. `signed_out`: the password or sign-in no longer works, so a
    person must connect again (no point retrying)."""

    def __init__(self, message: str, *, signed_out: bool = False):
        super().__init__(message)
        self.signed_out = signed_out


@dataclass
class Fetched:
    provider_id: str
    raw: bytes
    received_at: datetime | None = None


@dataclass
class Batch:
    emails: list[Fetched] = field(default_factory=list)
    cursor: dict[str, Any] = field(default_factory=dict)
    more: bool = False  # more waiting: check again soon


def _context(tenant_id: uuid.UUID, mailbox_id: uuid.UUID) -> str:
    return f"mailbox:{tenant_id}:{mailbox_id}"


def seal(mb: Mailbox, data: dict[str, Any]) -> None:
    mb.secret_enc = secrets.encrypt(json.dumps(data), context=_context(mb.tenant_id, mb.id))


def unseal(mb: Mailbox) -> dict[str, Any]:
    return json.loads(secrets.decrypt(mb.secret_enc, context=_context(mb.tenant_id, mb.id)))
