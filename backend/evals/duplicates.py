"""Seeded duplicate test set for F12.1 ("seeded duplicate set: >=95% auto-resolved
correctly, zero wrong merges").

The set is synthetic (decision S4, default "a"): Indian manufacturing customers
with planted duplicates of known kinds and planted look-alikes that must NOT be
merged. Deterministic for a given seed.

Run the full report against the dev database (creates a throwaway tenant):
    uv run python -m evals.duplicates --seed 7 --companies 400
"""

import argparse
import asyncio
import random
import string
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, MatchCandidate
from app.steward.identifiers import gstin_check_digit
from app.steward.records import create_account
from app.steward.service import process_record

FIRST = ["Shree", "Sri", "Om", "Jai", "New", "Royal", "National", "Supreme", "Universal",
         "Bharat", "Hindustan", "Deccan", "Western", "Eastern", "Kaveri", "Narmada", "Ganga",
         "Laxmi", "Ganesh", "Sai", "Balaji", "Krishna", "Mahavir", "Ambika", "Saraswati",
         "Vardhman", "Kirti", "Prakash", "Rajdeep", "Sunrise", "Galaxy", "Apex", "Zenith",
         "Precision", "Perfect", "Accurate", "Dynamic", "Vishwa", "Swastik", "Trimurti"]
SECOND = ["Forgings", "Castings", "Pumps", "Valves", "Polymers", "Fasteners", "Hydraulics",
          "Bearings", "Gears", "Transformers", "Cables", "Switchgear", "Compressors",
          "Extrusions", "Packaging", "Chemicals", "Alloys", "Tooling", "Motors", "Pipes",
          "Conveyors", "Boilers", "Filters", "Springs", "Electricals"]
THIRD = ["Engineering Works", "Industries", "Enterprises", "Manufacturing Co",
         "Engineers", "Products", "Systems", "Works", "Technologies", ""]
SUFFIX = ["Pvt Ltd", "Private Limited", "Ltd", "Limited", "LLP", ""]
CITIES = [  # city, state code, pincode prefix
    ("Pune", "27", "4110"), ("Mumbai", "27", "4000"), ("Nashik", "27", "4220"),
    ("Rajkot", "24", "3600"), ("Ahmedabad", "24", "3800"), ("Coimbatore", "33", "6410"),
    ("Chennai", "33", "6000"), ("Bengaluru", "29", "5600"), ("Hyderabad", "36", "5000"),
    ("Ludhiana", "03", "1410"), ("Faridabad", "06", "1210"), ("Kolkata", "19", "7000"),
    ("Jaipur", "08", "3020"), ("Indore", "23", "4520"), ("Noida", "09", "2013"),
]
ABBREV = {"Engineering": "Engg.", "Industries": "Inds.", "Manufacturing": "Mfg.",
          "Private Limited": "Pvt. Ltd.", "Pvt Ltd": "Private Limited", "Limited": "Ltd.",
          "Ltd": "Limited", "Enterprises": "Ent.", "Technologies": "Tech",
          "Electricals": "Elec."}
SPELLING = {"Shree": "Shri", "Sri": "Shree", "Laxmi": "Lakshmi", "Bharat": "Bharath",
            "Ganesh": "Ganesha", "Krishna": "Krsna", "Vishwa": "Vishva", "Kaveri": "Cauvery",
            "Saraswati": "Saraswathi", "Mahavir": "Mahaveer"}


@dataclass
class Row:
    key: str  # ground truth: which real company this row describes
    kind: str  # "base", a duplicate type, or a look-alike type
    data: dict = field(default_factory=dict)


def _pan(rng: random.Random, holder: str = "C") -> str:
    letters = string.ascii_uppercase
    return ("".join(rng.choice(letters) for _ in range(3)) + holder + rng.choice(letters)
            + "".join(rng.choice(string.digits) for _ in range(4)) + rng.choice(letters))


def _gstin(state: str, pan: str) -> str:
    first = f"{state}{pan}1Z"
    return first + gstin_check_digit(first)


def _name(rng: random.Random) -> str:
    parts = [rng.choice(FIRST), rng.choice(SECOND), rng.choice(THIRD), rng.choice(SUFFIX)]
    return " ".join(p for p in parts if p)


def _vary_name(rng: random.Random, name: str) -> str:
    out = name
    for a, b in rng.sample(list(ABBREV.items()), k=len(ABBREV)):
        if a in out and rng.random() < 0.7:
            out = out.replace(a, b, 1)
    for a, b in SPELLING.items():
        if out.startswith(a) and rng.random() < 0.6:
            out = b + out[len(a):]
    if rng.random() < 0.3:
        out = "M/s. " + out
    if out == name:
        out = name.upper() if rng.random() < 0.5 else name + " (India)"
    return out


def generate(seed: int = 7, companies: int = 400, dups_per_type: int = 40,
             lookalikes_per_type: int = 20) -> list[Row]:
    rng = random.Random(seed)
    rows: list[Row] = []
    names_used: set[str] = set()
    domains_used: set[str] = set()
    base: list[Row] = []
    for i in range(companies):
        name = _name(rng)
        while name in names_used:
            name = _name(rng)
        names_used.add(name)
        city, state, pin = rng.choice(CITIES)
        pan = _pan(rng, rng.choice("CCCCF"))
        domain = None
        if rng.random() < 0.5:  # two different companies never share a website
            domain = name.split()[0].lower() + name.split()[1].lower() + ".in"
            while domain in domains_used:
                domain = domain.replace(".in", "") + str(rng.randint(1, 9)) + ".in"
            domains_used.add(domain)
        r = Row(key=f"C{i}", kind="base", data={
            "name": name, "gstin": _gstin(state, pan), "city": city,
            "pincode": pin + f"{rng.randint(1, 99):02d}",
            "phone": f"9{rng.randint(100000000, 999999999)}",
            "website": domain, "industry": name.split()[1]})
        base.append(r)
    rows += base

    pool = rng.sample(base, k=min(len(base), dups_per_type * 5))
    groups = [pool[i * dups_per_type:(i + 1) * dups_per_type] for i in range(5)]
    for b in groups[0]:  # same GSTIN, name written differently
        rows.append(Row(b.key, "dup_same_gstin", {**b.data, "name": _vary_name(rng, b.data["name"]),
                                                  "phone": None}))
    for b in groups[1]:  # same PAN, registration in another state (branch GSTIN)
        other_state = rng.choice([c for c in CITIES if c[1] != b.data["gstin"][:2]])
        rows.append(Row(b.key, "dup_other_state_gstin", {
            "name": _vary_name(rng, b.data["name"]), "city": other_state[0],
            "gstin": _gstin(other_state[1], b.data["gstin"][2:12])}))
    for b in groups[2]:  # no GSTIN, but same phone (lead captured from a call/email)
        rows.append(Row(b.key, "dup_no_gstin_same_phone", {
            "name": _vary_name(rng, b.data["name"]), "phone": b.data["phone"],
            "city": b.data["city"]}))
    for b in groups[3]:  # no GSTIN, name variant only, same city and pincode
        rows.append(Row(b.key, "dup_no_ids", {
            "name": _vary_name(rng, b.data["name"]), "city": b.data["city"],
            "pincode": b.data["pincode"]}))
    for b in groups[4]:  # GSTIN typed with one character wrong
        g = list(b.data["gstin"])
        pos = rng.choice([7, 8, 9, 10])
        g[pos] = str((int(g[pos]) + 1) % 10)
        rows.append(Row(b.key, "dup_gstin_typo", {**b.data, "gstin": "".join(g),
                                                  "name": _vary_name(rng, b.data["name"])}))

    others = [b for b in base if b not in pool]
    la = rng.sample(others, k=min(len(others), lookalikes_per_type * 3))
    for i, b in enumerate(la[:lookalikes_per_type]):  # same name, other company, other city
        city, state, pin = rng.choice([c for c in CITIES if c[0] != b.data["city"]])
        rows.append(Row(f"L{i}a", "lookalike_same_name_other_pan", {
            "name": b.data["name"], "gstin": _gstin(state, _pan(rng)), "city": city,
            "pincode": pin + "01"}))
    for i, b in enumerate(la[lookalikes_per_type:2 * lookalikes_per_type]):
        # same first word and product, different company in the same city, no GSTIN
        w = b.data["name"].split()
        rows.append(Row(f"L{i}b", "lookalike_similar_name_same_city", {
            "name": f"{w[0]} {w[1]} {rng.choice(['Traders', 'Agencies', 'Sales Corporation'])}",
            "city": b.data["city"]}))
    for i, b in enumerate(la[2 * lookalikes_per_type:]):  # sister company: same group word
        w = b.data["name"].split()
        other_product = rng.choice([s for s in SECOND if s != w[1]])
        city, state, _ = rng.choice(CITIES)
        rows.append(Row(f"L{i}c", "lookalike_sister_company", {
            "name": f"{w[0]} {other_product} Ltd", "gstin": _gstin(state, _pan(rng)),
            "city": city}))
    rng.shuffle(rows)
    return rows


async def load_and_resolve(session: AsyncSession, tenant_id: uuid.UUID,
                           rows: list[Row]) -> dict[uuid.UUID, Row]:
    """Load like a migration import: create everything, then let the steward run."""
    ids: dict[uuid.UUID, Row] = {}
    for r in rows:
        acc = await create_account(session, tenant_id, {k: v for k, v in r.data.items() if v},
                                   source="import", actor="eval", strict=False,
                                   publish_event=False)
        ids[acc.id] = r
    for rid in list(ids):
        await process_record(session, "account", rid)
    return ids


async def evaluate(session: AsyncSession, ids: dict[uuid.UUID, Row]) -> dict:
    accounts = {a.id: a for a in await session.scalars(
        select(Account).where(Account.id.in_(list(ids))))}

    def root(aid: uuid.UUID) -> uuid.UUID:
        while accounts[aid].merged_into_id is not None:
            aid = accounts[aid].merged_into_id
        return aid

    keys_per_root: dict[uuid.UUID, set[str]] = defaultdict(set)
    for aid, row in ids.items():
        keys_per_root[root(aid)].add(row.key)
    wrong_merges = sum(len(k) - 1 for k in keys_per_root.values())

    open_pairs = {(c.left_id, c.right_id) for c in await session.scalars(
        select(MatchCandidate).where(MatchCandidate.status == "open",
                                     MatchCandidate.record_type == "account"))}

    def queued(x, y):
        rx, ry = root(x), root(y)
        return any({a, b} & {x, rx} and {a, b} & {y, ry} for a, b in open_pairs)

    by_key: dict[str, list[uuid.UUID]] = defaultdict(list)
    for aid, row in ids.items():
        by_key[row.key].append(aid)
    outcome: dict[str, Counter] = defaultdict(Counter)
    for aid, row in ids.items():
        if row.kind.startswith("dup_"):
            base_id = next(x for x in by_key[row.key] if ids[x].kind == "base")
            if root(aid) == root(base_id):
                outcome[row.kind]["auto_merged"] += 1
            elif queued(aid, base_id):
                outcome[row.kind]["sent_to_review"] += 1
            else:
                outcome[row.kind]["missed"] += 1
        elif row.kind.startswith("lookalike_"):
            # its look-alike partner is any other key's record with the same first name word
            partner = [x for x in ids if ids[x].kind == "base"
                       and ids[x].data["name"].split()[0] == row.data["name"].split()[0]]
            if any(root(aid) == root(p) for p in partner):
                outcome[row.kind]["wrongly_merged"] += 1
            elif any(queued(aid, p) for p in partner):
                outcome[row.kind]["sent_to_review"] += 1
            else:
                outcome[row.kind]["kept_apart"] += 1

    def rate(kinds, good):
        total = sum(sum(outcome[k].values()) for k in kinds)
        return round(100 * sum(outcome[k][good] for k in kinds) / total, 1) if total else None

    dup_kinds = sorted(k for k in outcome if k.startswith("dup_"))
    la_kinds = sorted(k for k in outcome if k.startswith("lookalike_"))
    with_ids = ["dup_same_gstin", "dup_other_state_gstin"]
    auto_ok = sum(outcome[k]["auto_merged"] for k in dup_kinds) + sum(
        outcome[k]["kept_apart"] for k in la_kinds)
    total = sum(sum(outcome[k].values()) for k in dup_kinds + la_kinds)
    return {
        "records": len(ids),
        "wrong_merges": wrong_merges,
        "auto_resolved_pct": round(100 * auto_ok / total, 1) if total else None,
        "auto_merged_pct_dups_with_ids": rate(with_ids, "auto_merged"),
        "auto_merged_pct_all_dups": rate(dup_kinds, "auto_merged"),
        "review_queue_size": len(open_pairs),
        "by_type": {k: dict(v) for k, v in sorted(outcome.items())},
    }


def format_report(res: dict) -> str:
    lines = [
        f"Records loaded: {res['records']}",
        f"Wrong merges: {res['wrong_merges']}",
        f"Auto-resolved correctly (all planted cases): {res['auto_resolved_pct']}%",
        f"Duplicates auto-merged where both carry GSTIN/PAN: "
        f"{res['auto_merged_pct_dups_with_ids']}%",
        f"Duplicates auto-merged overall: {res['auto_merged_pct_all_dups']}%",
        f"Open review candidates: {res['review_queue_size']}",
        "", "| Case | Outcome |", "|---|---|",
    ]
    for k, v in res["by_type"].items():
        lines.append(f"| {k} | " + ", ".join(f"{n} {o}" for o, n in sorted(v.items())) + " |")
    return "\n".join(lines)


async def _main(seed: int, companies: int, name_only: bool) -> None:
    from app.cli import create_tenant
    from app.config import get_settings

    get_settings().steward_name_only_auto_merge = name_only
    from app.db import dispose_engine, get_sessionmaker, tenant_session
    from app.models import Tenant

    slug = f"eval{uuid.uuid4().hex[:6]}"
    await create_tenant("Duplicate eval", slug, f"eval@{slug}.example.com", "Eval", "x" * 12)
    async with get_sessionmaker()() as s:
        tid = (await s.scalar(select(Tenant).where(Tenant.slug == slug))).id
    rows = generate(seed, companies, dups_per_type=companies // 10,
                    lookalikes_per_type=companies // 20)
    async with tenant_session(tid) as s:
        ids = await load_and_resolve(s, tid, rows)
    async with tenant_session(tid) as s:
        print(format_report(await evaluate(s, ids)))
    await dispose_engine()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--companies", type=int, default=400)
    p.add_argument("--name-only-auto-merge", action="store_true",
                   help="evaluate decision S3 option b")
    a = p.parse_args()
    asyncio.run(_main(a.seed, a.companies, a.name_only_auto_merge))
