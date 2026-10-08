"""The contract-test suite every adapter must pass to count as certified (F12.6).

The checks are generic: they need no knowledge of the data in the system, so the
same suite runs against the fake ERPNext in CI and against a real site:

    uv run python -m app.integrations.testing.contract erpnext \\
        --url http://localhost:8085 --key KEY --secret SECRET

Each check returns nothing on success and raises AssertionError saying what is wrong.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from app.integrations.adapter import Adapter, ConnectorError
from app.integrations.contracts import CONTRACTS, Entity, Record

PAGE = 3  # small on purpose, so paging is exercised even on a small data set


@dataclass
class Target:
    """How to build the adapter under test, with a good login and with broken ones."""

    name: str
    make: Callable[[], Adapter]
    make_bad_login: Callable[[], Adapter]
    make_unreachable: Callable[[], Adapter]


async def _all(adapter: Adapter, entity: Entity, **kw) -> tuple[list[Record], int]:
    records: list[Record] = []
    cursor, problems = None, 0
    for _ in range(10_000):
        page = await adapter.changes(entity, since=kw.get("since"), cursor=cursor, limit=PAGE)
        records += page.records
        problems += len(page.problems)
        for p in page.problems:
            assert p.reason, f"{entity}: a problem record has no reason"
        cursor = page.next_cursor
        if not cursor:
            return records, problems
    raise AssertionError(f"{entity}: paging never ended")


async def check_connects(t: Target) -> None:
    a = t.make()
    try:
        result = await a.check()
        assert result.ok, f"check() failed: {result.cause} {result.message}"
        assert result.about.get("user"), "check() did not say which user we act as"
    finally:
        await a.aclose()


async def check_bad_login(t: Target) -> None:
    a = t.make_bad_login()
    try:
        result = await a.check()
        assert not result.ok and result.cause == "auth_rejected", (
            f"a wrong key gave {result.cause!r}, expected 'auth_rejected'"
        )
        entity = next(iter(sorted(a.reads)))
        try:
            await a.changes(entity, since=None, cursor=None, limit=PAGE)
        except ConnectorError as e:
            assert e.cause == "auth_rejected", f"reading with a wrong key gave {e.cause!r}"
        else:
            raise AssertionError("reading with a wrong key did not fail")
    finally:
        await a.aclose()


async def check_unreachable(t: Target) -> None:
    a = t.make_unreachable()
    try:
        result = await a.check()
        assert not result.ok and result.cause in ("unreachable", "timeout"), (
            f"an unreachable system gave {result.cause!r}"
        )
    finally:
        await a.aclose()


async def check_listing(t: Target) -> None:
    """Paging returns every record exactly once, the same set reconciliation sees."""
    a = t.make()
    try:
        for entity in sorted(a.reads):
            records, problems = await _all(a, entity)
            contract = CONTRACTS[entity]
            ids = [r.external_id for r in records]
            assert len(ids) == len(set(ids)), f"{entity}: paging repeated records"
            for r in records:
                assert isinstance(r, contract), f"{entity}: got {type(r).__name__}"
                assert r.modified_at.utcoffset() == timedelta(0), f"{entity}: time not in UTC"
            stamps, cursor = [], None
            while True:
                page, cursor = await a.stamps(entity, cursor=cursor, limit=PAGE * 2)
                stamps += page
                if not cursor:
                    break
            stamp_ids = {s.external_id for s in stamps}
            assert len(stamp_ids) == len(stamps), f"{entity}: stamps repeated records"
            missing = stamp_ids - set(ids)
            assert len(missing) <= problems, f"{entity}: paging skipped {sorted(missing)[:5]}"
            extra = set(ids) - stamp_ids
            assert not extra, f"{entity}: stamps missed {sorted(extra)[:5]}"
    finally:
        await a.aclose()


async def check_since(t: Target) -> None:
    """changes(since=t) returns exactly the records modified at or after t."""
    a = t.make()
    try:
        for entity in sorted(a.reads):
            records, _ = await _all(a, entity)
            if len(records) < 2:
                continue
            times = sorted(r.modified_at for r in records)
            since = times[len(times) // 2]
            later, _ = await _all(a, entity, since=since)
            expected = {r.external_id for r in records if r.modified_at >= since}
            got = {r.external_id for r in later}
            assert got == expected, (
                f"{entity}: since={since.isoformat()} returned {len(got)} records, expected "
                f"{len(expected)}"
            )
    finally:
        await a.aclose()


async def check_fetch(t: Target) -> None:
    """fetch() returns the same record as the listing, and None for an unknown id."""
    a = t.make()
    try:
        for entity in sorted(a.reads):
            records, _ = await _all(a, entity)
            for r in records[:3]:
                got = await a.fetch(entity, r.external_id)
                assert got == r, f"{entity}: fetch({r.external_id!r}) differs from the listing"
            assert await a.fetch(entity, "forge-contract-test-no-such-record") is None, (
                f"{entity}: fetch of an unknown id did not return None"
            )
    finally:
        await a.aclose()


CHECKS: dict[str, Callable[[Target], Awaitable[None]]] = {
    "connects": check_connects,
    "bad_login": check_bad_login,
    "unreachable": check_unreachable,
    "listing": check_listing,
    "since": check_since,
    "fetch": check_fetch,
}


async def run(t: Target) -> dict[str, str | None]:
    """Every check's result: None when it passed, else what went wrong."""
    results: dict[str, str | None] = {}
    for name, check in CHECKS.items():
        try:
            await check(t)
            results[name] = None
        except AssertionError as e:
            results[name] = str(e) or "failed"
        except ConnectorError as e:
            results[name] = f"{e.cause}: {e.message}"
    return results


def _erpnext_target(url: str, key: str, secret: str, time_zone: str) -> Target:
    from app.integrations.erpnext.adapter import ERPNextAdapter, ERPNextSettings

    return Target(
        name=f"ERPNext at {url}",
        make=lambda: ERPNextAdapter(ERPNextSettings(url, key, secret, time_zone)),
        make_bad_login=lambda: ERPNextAdapter(
            ERPNextSettings(url, key, secret + "-wrong", time_zone)
        ),
        make_unreachable=lambda: ERPNextAdapter(
            ERPNextSettings("http://127.0.0.1:9", key, secret, time_zone)
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the adapter contract tests")
    parser.add_argument("adapter", choices=["erpnext"])
    parser.add_argument("--url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--time-zone", default="Asia/Kolkata")
    args = parser.parse_args()
    target = _erpnext_target(args.url, args.key, args.secret, args.time_zone)
    results = asyncio.run(run(target))
    print(f"Contract tests for {target.name}")
    for name, problem in results.items():
        verdict = "PASS" if problem is None else "FAIL"
        print(f"  {verdict}  {name}" + (f": {problem}" if problem else ""))
    sys.exit(0 if all(p is None for p in results.values()) else 1)


if __name__ == "__main__":
    main()
