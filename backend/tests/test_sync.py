"""F12.6 sync loop: paging from a high-water mark, resume after a stop, retries,
dead-letter, and the nightly reconciliation. Runs on the fake ERPNext (and on a real
site when FORGE_ERPNEXT_URL / _KEY / _SECRET are set)."""

import os
from datetime import timedelta

import httpx
import pytest

from app.integrations.contracts import Entity
from app.integrations.erpnext.adapter import ERPNextAdapter, ERPNextSettings
from app.integrations.sync import FlowState, MemorySink, RetryPolicy, reconcile, run_flow
from app.integrations.testing.fake_erpnext import FakeERPNext
from app.integrations.testing.seed import seed_demo

NO_OVERLAP = timedelta(0)


class FlakyERPNext(FakeERPNext):
    """Fails the next `fail_next` requests with `fault`, then answers normally."""

    fail_next = 0
    flaky_fault = "server_error"

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.fail_next > 0:
            self.fail_next -= 1
            self.fault = self.flaky_fault
            try:
                return await super().handle_async_request(request)
            finally:
                self.fault = None
        return await super().handle_async_request(request)


def setup(**seed_kw):
    fake = FlakyERPNext()
    seed_demo(fake, **seed_kw)
    adapter = ERPNextAdapter(
        ERPNextSettings("http://erp.test", fake.api_key, fake.api_secret), transport=fake
    )
    return fake, adapter, MemorySink()


class Sleeps(list):
    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


async def test_first_run_reads_everything_then_only_changes():
    fake, a, sink = setup()
    first = await run_flow(a, Entity.customer, sink, FlowState(), page_size=3)
    assert first.ok and first.read == len(fake.docs["Customer"]) and first.pages > 2
    held = sink.records[Entity.customer].values()
    assert first.state.watermark == max(r.modified_at for r in held)

    fake.touch("Customer", "Shakti Hydraulics", customer_group="OEM")
    fake.touch("Customer", "Anand Auto Electricals", payment_terms="Net 45")
    second = await run_flow(a, Entity.customer, sink, first.state, page_size=3, overlap=NO_OVERLAP)
    assert second.ok
    # Changed records, plus the one sitting exactly on the old high-water mark.
    assert second.read == 3
    assert sink.records[Entity.customer]["Shakti Hydraulics"].customer_group == "OEM"
    assert sink.records[Entity.customer]["Anand Auto Electricals"].payment_terms == "Net 45"
    assert second.state.watermark > first.state.watermark


async def test_overlap_window_rereads_recent_records():
    fake, a, sink = setup()
    first = await run_flow(a, Entity.customer, sink, FlowState())
    again = await run_flow(a, Entity.customer, sink, first.state, overlap=timedelta(seconds=3))
    assert 1 < again.read < first.read  # only the last few seconds, re-read harmlessly
    assert len(sink.records[Entity.customer]) == first.read


async def test_a_stopped_run_resumes_from_its_last_page():
    fake, a, sink = setup(burst=12)
    total = len(fake.docs["Customer"])
    part = await run_flow(a, Entity.customer, sink, FlowState(), page_size=5, max_pages=2)
    assert not part.finished and part.read == 10 and part.state.resuming

    fake.fault = "offline"
    sleeps = Sleeps()
    failed = await run_flow(a, Entity.customer, sink, part.state, page_size=5, sleep=sleeps)
    assert failed.error.cause == "unreachable" and failed.read == 0
    assert sleeps == [2.0, 4.0, 8.0] and failed.retries == 3
    assert failed.state == part.state  # nothing lost, nothing skipped

    fake.fault = None
    rest = await run_flow(a, Entity.customer, sink, failed.state, page_size=5)
    assert rest.ok and rest.read == total - 10
    assert len(sink.records[Entity.customer]) == total


async def test_failures_that_may_pass_are_retried():
    fake, a, sink = setup()
    fake.fail_next, fake.flaky_fault = 2, "rate_limited"
    sleeps = Sleeps()
    result = await run_flow(a, Entity.item, sink, FlowState(), sleep=sleeps)
    assert result.ok and result.retries == 2 and sleeps == [2.0, 4.0]
    assert result.read == len(fake.docs["Item"])


async def test_a_revoked_key_stops_at_once_with_its_cause():
    fake, a, sink = setup()
    fake.revoke()
    sleeps = Sleeps()
    result = await run_flow(a, Entity.item, sink, FlowState(), sleep=sleeps)
    assert result.error.cause == "auth_rejected" and result.retries == 0 and sleeps == []
    assert "rejected the API key" in result.error.message


async def test_waits_are_capped():
    fake, a, sink = setup()
    fake.fault = "timeout"
    sleeps = Sleeps()
    policy = RetryPolicy(attempts=6, first_wait=10, max_wait=30)
    result = await run_flow(a, Entity.item, sink, FlowState(), retry=policy, sleep=sleeps)
    assert result.error.cause == "timeout" and sleeps == [10, 20, 30, 30, 30]


async def test_unreadable_records_go_to_the_dead_letter_list_and_the_run_finishes():
    fake, a, sink = setup(broken=True)
    result = await run_flow(a, Entity.order, sink, FlowState(), page_size=4)
    assert result.ok and result.problems == 1
    assert [p.external_id for p in sink.dead[Entity.order]] == ["SAL-ORD-2026-BROKEN"]
    assert "SAL-ORD-2026-BROKEN" not in sink.records[Entity.order]


async def test_an_older_copy_never_replaces_a_newer_one():
    fake, a, sink = setup()
    await run_flow(a, Entity.item, sink, FlowState())
    old = sink.records[Entity.item]["GBX-110"]
    fake.touch("Item", "GBX-110", item_name="Helical gearbox 110 mm (revised)")
    new = await a.fetch(Entity.item, "GBX-110")
    await sink.put(Entity.item, [new])
    await sink.put(Entity.item, [old])
    assert sink.records[Entity.item]["GBX-110"].name.endswith("(revised)")


async def test_reconciliation_finds_and_repairs_drift():
    fake, a, sink = setup()
    await run_flow(a, Entity.item, sink, FlowState())
    assert (await reconcile(a, Entity.item, sink)).in_step

    del sink.records[Entity.item]["MTR-7K5"]  # missed by the sync
    fake.docs["Item"]["CPL-F50"].update(item_name="Coupling F50 (new)", modified=fake.tick())
    stale_watermark = sink.records[Entity.item]["CPL-F50"].modified_at
    del fake.docs["Item"]["SPR-KIT"]  # deleted in the ERP

    rec = await reconcile(a, Entity.item, sink)
    assert rec.checked == len(fake.docs["Item"])
    assert (rec.missing, rec.stale, rec.gone) == (["MTR-7K5"], ["CPL-F50"], ["SPR-KIT"])
    assert rec.repaired == 2 and not rec.unrepaired
    assert sink.records[Entity.item]["CPL-F50"].name == "Coupling F50 (new)"
    assert sink.records[Entity.item]["CPL-F50"].modified_at > stale_watermark
    again = await reconcile(a, Entity.item, sink)
    assert (again.missing, again.stale, again.gone) == ([], [], ["SPR-KIT"])


async def test_reconciliation_leaves_large_gaps_for_a_full_reread():
    fake, a, sink = setup()
    rec = await reconcile(a, Entity.stock, sink, repair_limit=5)
    assert len(rec.missing) == len(fake.docs["Bin"]) and rec.repaired == 5
    assert len(rec.unrepaired) == len(rec.missing) - 5


async def test_reconciliation_reports_a_broken_connection():
    fake, a, sink = setup()
    fake.revoke()
    rec = await reconcile(a, Entity.item, sink)
    assert rec.error.cause == "auth_rejected" and not rec.in_step


@pytest.mark.skipif(not os.environ.get("FORGE_ERPNEXT_URL"), reason="no live ERPNext configured")
async def test_full_sync_and_reconciliation_on_live_erpnext():
    a = ERPNextAdapter(ERPNextSettings(
        os.environ["FORGE_ERPNEXT_URL"], os.environ["FORGE_ERPNEXT_KEY"],
        os.environ["FORGE_ERPNEXT_SECRET"], os.environ.get("FORGE_ERPNEXT_TZ", "Asia/Kolkata"),
    ))
    sink = MemorySink()
    for entity in sorted(a.reads):
        result = await run_flow(a, entity, sink, FlowState(), page_size=4)
        assert result.ok, (entity, result.error)
        rec = await reconcile(a, entity, sink)
        assert rec.in_step, (entity, rec)
    await a.aclose()
