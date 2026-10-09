"""The adapter interface (F12.6 Connector SDK).

An adapter only talks to one kind of system and translates its records into the
shared contracts (app/integrations/contracts.py). Everything else (schedules,
retries, dead-letter tasks, health, reconciliation, the policy gate on writes) is
done once by the sync engine for every adapter, so a new adapter writes none of it.

Rules an adapter follows, checked by the contract-test suite
(app/integrations/testing/contract.py):
- Problems reaching the system raise ConnectorError with one of the CAUSES below,
  in plain words, so an alert can say exactly what is wrong.
- One record that cannot be translated never fails a page: it comes back in
  Page.problems with the raw data, and the rest of the page still arrives.
- changes() pages through every record modified at or after `since`, oldest first,
  each exactly once, even when many records share one modified time.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol, runtime_checkable

from app.integrations.contracts import Customer, Entity, NewCustomer, Record

# Why a call to the other system failed. The sync engine decides on retries and
# alerts from this alone.
CAUSES: dict[str, str] = {
    "auth_rejected": "The system rejected the login (key revoked, expired or wrong)",
    "permission_denied": "The login works but may not read or write this kind of record",
    "unreachable": "The system cannot be reached (address wrong, server down or network blocked)",
    "timeout": "The system did not answer in time",
    "rate_limited": "The system asked us to slow down",
    "server_error": "The system failed while handling the request",
    "bad_response": "The system answered with something we could not read",
    "rejected": "The system refused the record (a missing or invalid value)",
    "not_configured": "The connection is missing a setting",
}

RETRYABLE = frozenset({"unreachable", "timeout", "rate_limited", "server_error"})


class ConnectorError(Exception):
    def __init__(self, cause: str, message: str, *, detail: str | None = None):
        assert cause in CAUSES, cause
        self.cause = cause
        self.message = message
        self.detail = detail
        super().__init__(f"{cause}: {message}")

    @property
    def retryable(self) -> bool:
        return self.cause in RETRYABLE


@dataclass(frozen=True)
class ConnectionCheck:
    ok: bool
    cause: str | None = None
    message: str = ""
    # What the system told us about itself: versions, the user we act as, time zone.
    about: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordProblem:
    """A record that could not be translated into its contract."""

    entity: Entity
    external_id: str | None
    reason: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class Page:
    records: list[Record]
    # Opaque; pass back to get the next page. None when there is nothing more.
    next_cursor: str | None
    problems: list[RecordProblem] = field(default_factory=list)


@dataclass(frozen=True)
class Stamp:
    """What reconciliation compares: a record's id and when it last changed."""

    external_id: str
    modified_at: datetime


@runtime_checkable
class Adapter(Protocol):
    kind: ClassVar[str]  # "erpnext"
    label: ClassVar[str]  # "ERPNext"
    reads: ClassVar[frozenset[Entity]]

    async def check(self) -> ConnectionCheck:
        """Log in and ask who we are. Never raises; problems come back in the result."""
        ...

    async def changes(
        self, entity: Entity, *, since: datetime | None, cursor: str | None, limit: int
    ) -> Page:
        """Records modified at or after `since` (all when None), oldest first."""
        ...

    async def fetch(self, entity: Entity, external_id: str) -> Record | None:
        """One record by its id, or None when the system has no such record."""
        ...

    async def stamps(
        self, entity: Entity, *, cursor: str | None, limit: int
    ) -> tuple[list[Stamp], str | None]:
        """Every record's id and modified time, for the nightly reconciliation."""
        ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class CustomerMatch:
    """An existing ERP customer that may be the one Forge is about to create."""

    customer: Customer
    matched_on: str  # ref | gstin | name


@runtime_checkable
class CustomerWriter(Protocol):
    """Adapters that can create customers (decision E3). The engine around it does the
    duplicate checks, the policy gate, idempotency and the audit trail."""

    async def find_customers(
        self, *, ref: str | None, gstin: str | None, name: str | None
    ) -> list[CustomerMatch]:
        """Customers that could be the same one: carrying Forge's ref, the same GSTIN,
        or the same name."""
        ...

    async def create_customer(self, new: NewCustomer) -> Customer:
        """Create it and return it as the ERP now holds it."""
        ...

