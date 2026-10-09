"""The approval inbox (F12.3): card timing and what a decision led to."""

from app.db import tenant_session
from app.models import Decision
from tests.test_agents import API, ctx, drain, gstin, steward_handlers  # noqa: F401


async def test_card_timing_and_what_happened_next(client, ctx):  # noqa: F811
    h = ctx["h"]
    await client.put(f"{API}/agents/data_steward/level", headers=h, json={"level": 0})
    g = gstin("27", "AAPFU0939F")
    for name in ("Acme Pumps", "ACME Pumps Pvt Ltd"):
        r = await client.post(f"{API}/accounts", headers=h, json={"name": name, "gstin": g})
        assert r.status_code == 201, r.text
    await drain()
    await drain()
    card = (await client.get(f"{API}/decisions", headers=h)).json()[0]
    assert card["first_shown_at"] is None and card["decision_ms"] is None

    # The first time someone who can decide has it on screen is kept; later views don't move it.
    assert (await client.post(f"{API}/decisions/{card['id']}/shown", headers=h)).status_code == 204
    first = (await client.get(f"{API}/decisions/{card['id']}", headers=h)).json()["first_shown_at"]
    assert first
    await client.post(f"{API}/decisions/{card['id']}/shown", headers=h)
    assert (await client.get(f"{API}/decisions/{card['id']}", headers=h)).json()[
        "first_shown_at"] == first

    # A time that can't be right is refused; a real one is kept with the decision.
    bad = await client.post(f"{API}/decisions/{card['id']}/approve", headers=h,
                            json={"shown_ms": -5})
    assert bad.status_code == 422
    ok = await client.post(f"{API}/decisions/{card['id']}/approve", headers=h,
                           json={"shown_ms": 42_000, "note": "same GSTIN"})
    assert ok.json()["status"] == "approved" and ok.json()["decision_ms"] == 42_000
    async with tenant_session(ctx["tid"]) as s:
        assert (await s.get(Decision, ok.json()["id"])).decision_ms == 42_000

    # What happened next: the rules checked again, the merge itself, then a person undoing it.
    detail = (await client.get(f"{API}/decisions/{card['id']}", headers=h)).json()
    assert [r["kind"] for r in detail["next"]] == ["action.proposed", "record.merged",
                                                    "action.executed"]
    merge_id = detail["result"]["result"]["merge_event_id"]
    r = await client.post(f"{API}/steward/merges/{merge_id}/undo", headers=h,
                          json={"notes": "different plants"})
    assert r.status_code == 200, r.text
    detail = (await client.get(f"{API}/decisions/{card['id']}", headers=h)).json()
    assert [r["kind"] for r in detail["next"]][-2:] == ["action.executed", "merge.undone"]
