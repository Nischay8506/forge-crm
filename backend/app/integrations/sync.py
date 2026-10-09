"""The sync loop (F12.6 sync engine), written once for every adapter.

This part does not depend on where synced records are stored (open question E2):
records go to a Sink, and the caller keeps each flow's FlowState between runs. The
scheduler, tables, health page and alerts come once E2, E6 and E7 are answered.

- run_flow() reads one record type from one connection: page by page from the last
  high-water mark, retrying failures that may pass (unreachable, timeout, rate
  limited, server error) with growing waits, stopping at once on ones that will not
  (rejected key, missing permission). A crash or failure mid-run resumes from the
  saved cursor next time. Records that cannot be translated go to the sink's
  problem list (the dead-letter queue) and never stop the run.
- reconcile() is the nightly comparison: every record id and modified time in the
  ERP against what Forge holds, repairing what is missing or out of date.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.integrations.adapter import Adapter, ConnectorError, RecordProblem
from app.integrations.contracts import Entity, Record


class Sink(Protocol):
    """Where synced records go.

    put() must be idempotent: the same record may arrive more than once (overlap
    window, retries, reconciliation), and an older copy must never replace a newer one.
    """

    async def put(self, entity: Entity, records: list[Record]) -> None: ...

    async def problems(self, entity: Entity, problems: list[RecordProblem]) -> None: ...

    async def held(self, entity: Entity) -> dict[str, datetime]:
        """external_id -> modified_at of every record Forge holds for this flow."""
        ...


@dataclass(frozen=True)
class FlowState:
    """What a flow remembers between runs (stored per connection and record type)."""

    # Next run reads records modified at or after this (minus the overlap window).
    watermark: datetime | None = None
    # Set while a run is unfinished, so the next run resumes where it stopped.
    run_since: datetime | None = None
    cursor: str | None = None
    run_max: datetime | None = None

    @property
    def resuming(self) -> bool:
        return self.cursor is not None


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4  # tries per page, including the first
    first_wait: float = 2.0  # seconds; doubles each time
    max_wait: float = 60.0


DEFAULT_RETRY = RetryPolicy()


@dataclass
class RunResult:
    entity: Entity
    state: FlowState
    read: int = 0
    problems: int = 0
    pages: int = 0
    retries: int = 0
    error: ConnectorError | None = None
    finished: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.finished


# Records changed in the ERP while a run was reading can carry a modified time just
# before the run's high-water mark, so each run re-reads a short window. Re-reads are
# harmless (put() is idempotent); reconciliation catches anything older.
OVERLAP = timedelta(minutes=2)


async def _with_retries(
    call: Callable[[], Awaitable], policy: RetryPolicy, sleep, result: RunResult
):
    wait = policy.first_wait
    for attempt in range(1, policy.attempts + 1):
        try:
            return await call()
        except ConnectorError as e:
            if not e.retryable or attempt == policy.attempts:
                raise
            result.retries += 1
            await sleep(min(wait, policy.max_wait))
            wait *= 2
    raise AssertionError("unreachable")


async def run_flow(
    adapter: Adapter,
    entity: Entity,
    sink: Sink,
    state: FlowState,
    *,
    page_size: int = 200,
    max_pages: int | None = None,
    retry: RetryPolicy = DEFAULT_RETRY,
    overlap: timedelta = OVERLAP,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RunResult:
    """Read one record type into the sink. Never raises a ConnectorError: the result
    carries it, with a state that resumes from the last page saved."""
    if state.resuming:
        since, cursor, run_max = state.run_since, state.cursor, state.run_max
    else:
        since = state.watermark - overlap if state.watermark else None
        cursor, run_max = None, state.watermark
    result = RunResult(entity=entity, state=state)
    while max_pages is None or result.pages < max_pages:
        try:
            page = await _with_retries(
                lambda c=cursor: adapter.changes(entity, since=since, cursor=c, limit=page_size),
                retry, sleep, result,
            )
        except ConnectorError as e:
            result.error = e
            return result
        if page.records:
            await sink.put(entity, page.records)
            newest = max(r.modified_at for r in page.records)
            run_max = max(run_max, newest) if run_max else newest
        if page.problems:
            await sink.problems(entity, page.problems)
        result.read += len(page.records)
        result.problems += len(page.problems)
        result.pages += 1
        cursor = page.next_cursor
        if cursor is None:
            result.state = FlowState(watermark=run_max)
            result.finished = True
            return result
        # Saved after every page, so a stop here resumes from the next one.
        result.state = FlowState(
            watermark=state.watermark, run_since=since, cursor=cursor, run_max=run_max
        )
    return result


@dataclass
class Reconciliation:
    entity: Entity
    checked: int = 0
    missing: list[str] = field(default_factory=list)  # in the ERP, not in Forge
    stale: list[str] = field(default_factory=list)  # Forge's copy is older than the ERP's
    gone: list[str] = field(default_factory=list)  # in Forge, no longer in the ERP
    repaired: int = 0
    unrepaired: list[str] = field(default_factory=list)
    error: ConnectorError | None = None

    @property
    def in_step(self) -> bool:
        return not (self.missing or self.stale or self.gone) and self.error is None


async def reconcile(
    adapter: Adapter,
    entity: Entity,
    sink: Sink,
    *,
    repair_limit: int = 500,
    page_size: int = 1000,
    retry: RetryPolicy = DEFAULT_RETRY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Reconciliation:
    """Compare every record id and modified time with what Forge holds and fetch the
    missing or out-of-date ones (up to repair_limit; more means a full re-read is due).
    Records the ERP no longer has are reported in `gone`; what to do with them is
    decided with the storage (E2)."""
    rec = Reconciliation(entity=entity)
    scratch = RunResult(entity=entity, state=FlowState())
    erp: dict[str, datetime] = {}
    cursor = None
    try:
        while True:
            stamps, cursor = await _with_retries(
                lambda c=cursor: adapter.stamps(entity, cursor=c, limit=page_size),
                retry, sleep, scratch,
            )
            erp.update((s.external_id, s.modified_at) for s in stamps)
            if cursor is None:
                break
    except ConnectorError as e:
        rec.error = e
        return rec
    held = await sink.held(entity)
    rec.checked = len(erp)
    rec.missing = sorted(set(erp) - set(held))
    rec.stale = sorted(i for i in set(erp) & set(held) if held[i] < erp[i])
    rec.gone = sorted(set(held) - set(erp))
    todo = rec.missing + rec.stale
    rec.unrepaired = todo[repair_limit:]
    fixed: list[Record] = []
    for n, external_id in enumerate(todo[:repair_limit]):
        try:
            record = await _with_retries(
                lambda i=external_id: adapter.fetch(entity, i), retry, sleep, scratch
            )
        except ConnectorError as e:
            rec.error = e
            rec.unrepaired = todo[n:]
            break
        if record is not None:
            fixed.append(record)
    if fixed:
        await sink.put(entity, fixed)
    rec.repaired = len(fixed)
    return rec


class MemorySink:
    """A Sink in memory, for tests and dry runs. Keeps the newest copy of each record."""

    def __init__(self) -> None:
        self.records: dict[Entity, dict[str, Record]] = {e: {} for e in Entity}
        self.dead: dict[Entity, list[RecordProblem]] = {e: [] for e in Entity}
        self.puts = 0

    async def put(self, entity: Entity, records: list[Record]) -> None:
        self.puts += 1
        store = self.records[entity]
        for r in records:
            have = store.get(r.external_id)
            if have is None or have.modified_at <= r.modified_at:
                store[r.external_id] = r

    async def problems(self, entity: Entity, problems: list[RecordProblem]) -> None:
        self.dead[entity].extend(problems)

    async def held(self, entity: Entity) -> dict[str, datetime]:
        return {k: r.modified_at for k, r in self.records[entity].items()}

