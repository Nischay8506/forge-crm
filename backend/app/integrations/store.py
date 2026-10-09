"""Where synced ERP records live (decision E2): DbSink, the sync engine's Sink for the
erp_* tables.

- put() upserts by (connection, external id). A row only changes when the incoming
  copy is at least as new as the stored one, so re-reads, retries and reconciliation
  can never put an older copy back. A record that comes back after being marked gone
  is live again, and an open dead letter for it is resolved.
- problems() keeps one open dead letter per record, counting how often it failed.
- mark_gone() flags records the ERP no longer has; they are never deleted.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.adapter import RecordProblem
from app.integrations.contracts import Entity, Record
from app.models import SyncDeadLetter
from app.models.integrations import ERP_RECORD_MODELS

BASE_COLUMNS = ("external_id", "modified_at", "disabled")
# Never set from the ERP record: identity, bookkeeping, and the customer's link to an
# account (owned by app/integrations/linking.py).
SKIP = frozenset({"id", "tenant_id", "connection_id", "synced_at", "gone_at", "data",
                  "account_id", "link_status", "link_method", "link_reasons", "candidates",
                  "linked_by", "linked_at", *BASE_COLUMNS})


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


class DbSink:
    """A Sink writing to the erp_* tables of one connection inside one session.

    After a run, `changed` holds the row ids of records that were inserted or updated,
    per entity (linking uses the customers')."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID, connection_id: uuid.UUID):
        self.session = session
        self.tenant_id = tenant_id
        self.connection_id = connection_id
        self.changed: dict[Entity, list[uuid.UUID]] = {e: [] for e in Entity}
        self.too_long: list[RecordProblem] = []

    def _row(self, entity: Entity, model, r: Record) -> dict[str, Any] | RecordProblem:
        row: dict[str, Any] = {
            "id": uuid.uuid4(), "tenant_id": self.tenant_id,
            "connection_id": self.connection_id, "external_id": r.external_id,
            "modified_at": r.modified_at, "disabled": r.disabled,
            "data": _plain(r.model_dump(mode="json")),
        }
        for col in model.__table__.columns:
            if col.name in SKIP or not hasattr(r, col.name):
                continue
            value = getattr(r, col.name)
            limit = getattr(col.type, "length", None) if isinstance(col.type, String) else None
            if isinstance(value, str) and limit and len(value) > limit:
                return RecordProblem(entity, r.external_id,
                                     f"{col.name} is longer than {limit} characters: "
                                     f"{value[:40]!r}...", row["data"])
            row[col.name] = value
        if len(r.external_id) > 200:
            return RecordProblem(entity, r.external_id[:200], "the record id is longer than "
                                 "200 characters", row["data"])
        return row

    async def put(self, entity: Entity, records: list[Record]) -> None:
        model = ERP_RECORD_MODELS[entity.value]
        newest: dict[str, Record] = {}
        for r in records:  # one row per record, so the upsert never hits a row twice
            have = newest.get(r.external_id)
            if have is None or have.modified_at <= r.modified_at:
                newest[r.external_id] = r
        rows, problems = [], []
        for r in newest.values():
            row = self._row(entity, model, r)
            (problems if isinstance(row, RecordProblem) else rows).append(row)
        if problems:
            await self.problems(entity, problems)
        if not rows:
            return
        stmt = pg_insert(model).values(rows)
        columns = {k for row in rows for k in row} - {"id", "tenant_id", "connection_id",
                                                      "external_id"}
        stmt = stmt.on_conflict_do_update(
            index_elements=["connection_id", "external_id"],
            set_={**{c: stmt.excluded[c] for c in columns}, "synced_at": func.now(),
                  "gone_at": None},
            where=model.modified_at <= stmt.excluded.modified_at,
        ).returning(model.id, model.external_id)
        done = (await self.session.execute(stmt)).all()
        self.changed[entity].extend(row.id for row in done)
        if done:
            await self.session.execute(
                update(SyncDeadLetter)
                .where(SyncDeadLetter.connection_id == self.connection_id,
                       SyncDeadLetter.entity == entity.value, SyncDeadLetter.status == "open",
                       SyncDeadLetter.external_id.in_([row.external_id for row in done]))
                .values(status="resolved", resolved_by="system:sync",
                        resolved_at=datetime.now(UTC), note="Read successfully on a later sync")
                .execution_options(synchronize_session=False))

    async def problems(self, entity: Entity, problems: list[RecordProblem]) -> None:
        for p in problems:
            values = {"tenant_id": self.tenant_id, "connection_id": self.connection_id,
                      "entity": entity.value, "external_id": p.external_id,
                      "reason": p.reason[:2000], "raw": _plain(p.raw)}
            if p.external_id is None:
                self.session.add(SyncDeadLetter(id=uuid.uuid4(), **values))
                continue
            stmt = pg_insert(SyncDeadLetter).values(id=uuid.uuid4(), **values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["connection_id", "entity", "external_id"],
                index_where=SyncDeadLetter.status == "open",
                set_={"reason": stmt.excluded.reason, "raw": stmt.excluded.raw,
                      "attempts": SyncDeadLetter.attempts + 1, "last_seen_at": func.now()},
            )
            await self.session.execute(stmt)
        await self.session.flush()

    async def held(self, entity: Entity) -> dict[str, datetime]:
        model = ERP_RECORD_MODELS[entity.value]
        rows = await self.session.execute(
            select(model.external_id, model.modified_at).where(
                model.connection_id == self.connection_id, model.gone_at.is_(None)))
        return {r.external_id: r.modified_at for r in rows}

    async def mark_gone(self, entity: Entity, external_ids: list[str]) -> int:
        if not external_ids:
            return 0
        model = ERP_RECORD_MODELS[entity.value]
        rows = await self.session.execute(
            update(model).where(model.connection_id == self.connection_id,
                                model.external_id.in_(external_ids), model.gone_at.is_(None))
            .values(gone_at=datetime.now(UTC)).returning(model.id)
            .execution_options(synchronize_session=False))
        return len(rows.all())
