"""What a quote line is priced at, and the floor it is checked against (F3.3; C2 A).

A line's list price comes from the first of these that covers it:

  contract    a selling price in the ERP for this very customer, valid today
  price_list  the item's rate on the ERP connection's default selling price list
  cost_plus   the cost (F3.2) at the family's target margin
  none        nothing could price it; the line goes to a person

Then the adjustments, in this order: the account's tier discount, the quantity break the
line reaches, and the rep's own ask. A customer's own ERP price is already negotiated, so
only the rep's ask comes off it (Y6). The price is rounded up as the policy says, and the
margin is worked out against the line's cost and checked against the family's floor,
whichever way the price was found. A line whose cost is not known cannot pass the floor
on its own: it goes for approval, as does a line below its floor and a line nothing could
price. The whole quote is checked too, against the floor its lines weigh out to.

The numbers come from a price policy (app/models/cpq.PricePolicy): the family's own if
it has one, else the company-wide one. A policy is never edited; a change is a new row
from a date, so a quote made in March can still be explained with the numbers that
applied in March. Nothing is assumed: with no policy, a line that needs cost-plus is not
priced at all and says which screen to set it on.

Every line keeps its whole waterfall (where the list price came from, each discount with
its amount, the margin, the floor), so a price can always show why it is what it is.
Each waterfall is sealed like a cost is (F3.2): seal_of() fingerprints it and
price_revision() writes every line's seal into the audit chain.

Freight and insurance (K1 D, waiting on G1-G7) will join the waterfall here once the
freight answers arrive.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import ValidationFailed
from app.cpq import quotes, units
from app.cpq.costing import money, rs
from app.models import (
    Account,
    AuditRecord,
    ErpConnection,
    ErpCustomer,
    ErpPrice,
    PricePolicy,
    ProductFamily,
    Quote,
    QuoteLine,
    QuoteRevision,
)
from app.models.golden import ACCOUNT_TIERS

CENT = Decimal("0.01")
QTY_PLACES = Decimal("0.0001")
# Every price is a round number of paisa; a company can ask for rounder ones.
ROUNDINGS = ("0.01", "1", "5", "10")
PAISA = "0.01"
MAX_BREAKS = 20
MAX_PRICE = Decimal("1e12")
# A cost Forge trusts to price from: worked out in full, or accepted by a person (K10 A).
GOOD_COST = ("complete", "reviewed")
NEEDS_APPROVAL = ("below_floor", "no_cost", "no_price", "no_floor")
# What a new company can start from on the setup screen; never used on its own.
SAMPLE_POLICY = {
    "target_margin_pct": "25",
    "floor_margin_pct": "15",
    "tier_discounts": {"standard": "0", "key": "2", "strategic": "5"},
    "qty_breaks": [{"from_qty": "50", "discount_pct": "2"},
                   {"from_qty": "200", "discount_pct": "4"}],
    "rounding": "1",  # the step when a quote has "Round off" ticked (Y5)
}


def _dec(v: Any) -> Decimal | None:
    try:
        d = Decimal(str(v).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d if d.is_finite() else None


def pct(v: Decimal | None) -> str | None:
    """A percentage as it is shown: 5, 2.5, 12.75."""
    if v is None:
        return None
    s = format(v.quantize(Decimal("0.001"), ROUND_HALF_UP).normalize(), "f")
    return s if "E" not in s else format(v.quantize(Decimal("0.001")), "f")


# The policy -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    """The numbers a family is priced by on a day, and where they came from."""

    id: uuid.UUID | None
    family_id: uuid.UUID | None
    effective_from: date
    target_margin_pct: Decimal
    floor_margin_pct: Decimal
    tier_discounts: dict[str, Decimal]
    qty_breaks: tuple[tuple[Decimal, Decimal], ...]  # (from_qty, discount_pct), biggest last
    rounding: str

    @property
    def scope(self) -> str:
        return "family" if self.family_id else "company"

    def tier_discount(self, tier: str | None) -> Decimal:
        return self.tier_discounts.get(tier or "standard", Decimal(0))

    def qty_break(self, qty: Decimal | None) -> tuple[Decimal, Decimal] | None:
        """The highest band the quantity reaches, if any."""
        if qty is None:
            return None
        hit = None
        for from_qty, discount in self.qty_breaks:
            if qty >= from_qty:
                hit = (from_qty, discount)
        return hit

    def out(self) -> dict[str, Any]:
        return {"id": str(self.id) if self.id else None, "scope": self.scope,
                "from": self.effective_from.isoformat(),
                "target_margin_pct": pct(self.target_margin_pct),
                "floor_margin_pct": pct(self.floor_margin_pct),
                "rounding": self.rounding}


def _policy_of(row: PricePolicy) -> Policy:
    p = row.policy or {}
    breaks = sorted(
        ((_dec(b.get("from_qty")) or Decimal(0), _dec(b.get("discount_pct")) or Decimal(0))
         for b in (p.get("qty_breaks") or [])), key=lambda b: b[0])
    return Policy(
        id=row.id, family_id=row.family_id, effective_from=row.effective_from,
        target_margin_pct=_dec(p.get("target_margin_pct")) or Decimal(0),
        floor_margin_pct=_dec(p.get("floor_margin_pct")) or Decimal(0),
        tier_discounts={k: _dec(v) or Decimal(0)
                        for k, v in (p.get("tier_discounts") or {}).items()},
        qty_breaks=tuple(breaks), rounding=str(p.get("rounding") or "none"))


def check(data: dict[str, Any]) -> dict[str, Any]:
    """A policy as it will be stored, or what is wrong with it, field by field."""
    problems: list[str] = []
    out: dict[str, Any] = {}
    for key, word in (("target_margin_pct", "target margin"), ("floor_margin_pct", "floor")):
        v = _dec(data.get(key))
        if v is None:
            problems.append(f"{word}: a percentage is needed")
        elif not (0 <= v < 100):
            problems.append(f"{word}: {v} is not a percentage from 0 up to 100")
        else:
            out[key] = pct(v)
    if "target_margin_pct" in out and "floor_margin_pct" in out \
            and _dec(out["floor_margin_pct"]) > _dec(out["target_margin_pct"]):
        problems.append("floor: the floor cannot be above the target margin")
    tiers: dict[str, str] = {}
    for tier, v in (data.get("tier_discounts") or {}).items():
        if tier not in ACCOUNT_TIERS:
            problems.append(f"tier discounts: {tier} is not one of "
                            f"{', '.join(ACCOUNT_TIERS)}")
            continue
        d = _dec(v)
        if d is None or not (0 <= d < 100):
            problems.append(f"tier discounts, {tier}: a percentage from 0 up to 100 is needed")
        else:
            tiers[tier] = pct(d)
    out["tier_discounts"] = tiers
    breaks: list[dict[str, str]] = []
    rows = data.get("qty_breaks") or []
    if len(rows) > MAX_BREAKS:
        problems.append(f"quantity breaks: up to {MAX_BREAKS}")
        rows = rows[:MAX_BREAKS]
    seen: set[Decimal] = set()
    for i, b in enumerate(rows, start=1):
        q, d = _dec(b.get("from_qty")), _dec(b.get("discount_pct"))
        if q is None or q <= 0:
            problems.append(f"quantity break {i}: a quantity above 0 is needed")
        elif q in seen:
            problems.append(f"quantity break {i}: {pct(q)} is there twice")
        elif d is None or not (0 <= d < 100):
            problems.append(f"quantity break {i}: a percentage from 0 up to 100 is needed")
        else:
            seen.add(q)
            breaks.append({"from_qty": format(q.normalize(), "f"), "discount_pct": pct(d)})
    out["qty_breaks"] = sorted(breaks, key=lambda b: _dec(b["from_qty"]))
    rounding = str(data.get("rounding") or "0.01")
    if rounding not in ROUNDINGS:
        problems.append(f"rounding: one of {', '.join(ROUNDINGS)}")
    else:
        out["rounding"] = rounding
    if problems:
        raise ValidationFailed(problems)
    return out


async def save(session: AsyncSession, tenant_id: uuid.UUID, family_id: uuid.UUID | None,
               data: dict[str, Any], actor: str, *, effective_from: date,
               note: str | None = None) -> PricePolicy:
    """A new dated version of a policy (never an edit of one that exists)."""
    row = PricePolicy(tenant_id=tenant_id, family_id=family_id,
                      effective_from=effective_from, policy=check(data),
                      note=(note or None), created_by=actor)
    session.add(row)
    await session.flush()
    await chain.append(session, tenant_id, "price.policy.set", actor=actor,
                       record_type="price_policy", record_id=row.id,
                       evidence_class="customer_money",
                       data={"family_id": str(family_id) if family_id else None,
                             "from": effective_from.isoformat(), "policy": row.policy,
                             "note": note})
    return row


async def policies(session: AsyncSession, tenant_id: uuid.UUID, day: date,
                   family_ids: set[uuid.UUID]) -> dict[uuid.UUID | None, Policy]:
    """The policy in force on `day` for each family that has one, and the company-wide
    one under None. A family with no policy of its own is not in the answer."""
    rows = list(await session.scalars(
        select(PricePolicy).where(PricePolicy.tenant_id == tenant_id,
                                  PricePolicy.effective_from <= day)
        .order_by(PricePolicy.effective_from, PricePolicy.created_at)))
    out: dict[uuid.UUID | None, Policy] = {}
    for row in rows:  # oldest first, so the latest in force wins
        if row.family_id is None or row.family_id in family_ids:
            out[row.family_id] = _policy_of(row)
    return out


async def versions(session: AsyncSession, tenant_id: uuid.UUID,
                   family_id: uuid.UUID | None) -> list[PricePolicy]:
    return list(await session.scalars(
        select(PricePolicy).where(PricePolicy.tenant_id == tenant_id,
                                  PricePolicy.family_id == family_id)
        .order_by(PricePolicy.effective_from.desc(), PricePolicy.created_at.desc())))


# What it takes to price a revision ---------------------------------------------------------

@dataclass(frozen=True)
class FoundPrice:
    """A price found in an ERP, as the waterfall shows it."""

    source: str  # contract | price_list
    rate: Decimal
    uom: str | None
    price_list: str
    connection: str
    synced_at: datetime | None
    valid_upto: date | None


@dataclass
class Book:
    """Everything needed to price one revision, read once."""

    day: date
    currency: str
    tier: str | None
    account_name: str | None
    policies: dict[uuid.UUID | None, Policy] = field(default_factory=dict)
    families: dict[uuid.UUID, str] = field(default_factory=dict)
    prices: dict[str, list[ErpPrice]] = field(default_factory=dict)  # item code (upper) -> rows
    customer_ids: set[str] = field(default_factory=set)
    connections: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    round_off: bool = False  # the revision's own switch (Y5); the step is the policy's

    @classmethod
    async def load(cls, session: AsyncSession, q: Quote, lines: list[QuoteLine],
                   day: date) -> Book:
        account = await session.get(Account, q.account_id) if q.account_id else None
        family_ids = {ln.family_id for ln in lines if ln.family_id}
        book = cls(day=day, currency=q.currency, tier=account.tier if account else None,
                   account_name=account.name if account else None)
        book.policies = await policies(session, q.tenant_id, day, family_ids)
        if family_ids:
            book.families = {f.id: f.name for f in await session.scalars(
                select(ProductFamily).where(ProductFamily.id.in_(family_ids)))}
        if q.account_id:
            book.customer_ids = {x for x in await session.scalars(
                select(ErpCustomer.external_id).where(
                    ErpCustomer.account_id == q.account_id,
                    ErpCustomer.link_status == "linked", ErpCustomer.gone_at.is_(None)))}
        codes = {ln.item_code.upper() for ln in lines if ln.item_code}
        if codes:
            rows = list(await session.scalars(
                select(ErpPrice).where(ErpPrice.tenant_id == q.tenant_id,
                                       ErpPrice.gone_at.is_(None),
                                       ErpPrice.disabled.is_(False),
                                       ErpPrice.currency == q.currency)))
            for r in rows:
                if r.item_code.upper() in codes:
                    book.prices.setdefault(r.item_code.upper(), []).append(r)
        if book.prices:
            for c in await session.scalars(select(ErpConnection).where(
                    ErpConnection.tenant_id == q.tenant_id)):
                book.connections[c.id] = {
                    "name": c.name,
                    "selling_price_list": (c.settings or {}).get("selling_price_list")}
        return book

    def policy_for(self, family_id: uuid.UUID | None) -> Policy | None:
        if family_id is not None and family_id in self.policies:
            return self.policies[family_id]
        return self.policies.get(None)

    def _valid(self, r: ErpPrice) -> bool:
        return (r.valid_from is None or r.valid_from <= self.day) \
            and (r.valid_upto is None or r.valid_upto >= self.day)

    def _found(self, r: ErpPrice, source: str) -> FoundPrice:
        c = self.connections.get(r.connection_id) or {}
        return FoundPrice(source=source, rate=r.rate, uom=r.uom, price_list=r.price_list,
                          connection=c.get("name") or "the ERP", synced_at=r.synced_at,
                          valid_upto=r.valid_upto)

    def erp_price(self, ln: QuoteLine, notes: list[str]) -> FoundPrice | None:
        """The customer's own price first, then the connection's selling price list."""
        rows = [r for r in self.prices.get((ln.item_code or "").upper(), [])
                if self._valid(r)]
        if not rows:
            return None
        mine = [r for r in rows if r.customer_external_id
                and r.customer_external_id in self.customer_ids]
        if mine:
            return self._found(_latest(mine), "contract")
        lists = []
        for r in rows:
            if r.customer_external_id:
                continue
            want = (self.connections.get(r.connection_id) or {}).get("selling_price_list")
            if not want:
                notes.append(
                    f"{self.connections.get(r.connection_id, {}).get('name', 'The ERP')} has "
                    f"a price for this item on {r.price_list}, but no default selling price "
                    "list is set on the connection, so Forge does not guess which list to "
                    "use (Integrations → the connection → Default selling price list).")
                continue
            if r.price_list.strip().lower() == want.strip().lower():
                lists.append(r)
        return self._found(_latest(lists), "price_list") if lists else None


def _latest(rows: list[ErpPrice]) -> ErpPrice:
    """The one in force: the latest start, then the one changed last."""
    return sorted(rows, key=lambda r: (r.valid_from or date.min, r.modified_at))[-1]


# One line ----------------------------------------------------------------------------------

def _round(value: Decimal, rounding: str) -> Decimal:
    """Rounding only ever goes up, so it can never push a line under its floor."""
    step = Decimal(rounding if rounding in ROUNDINGS else PAISA)
    return (value / step).quantize(Decimal(1), ROUND_CEILING) * step


def _off(price: Decimal, pct_off: Decimal) -> tuple[Decimal, Decimal]:
    """A discount off a price kept to the paisa: the new price rounded up, and the cut as
    the difference, so every step of the waterfall adds up to the paisa it shows."""
    new = _round(price * (100 - pct_off) / 100, PAISA)
    return new, price - new


def _margin(price: Decimal, cost: Decimal) -> Decimal:
    return (price - cost) / price * 100


def price_line(book: Book, ln: QuoteLine) -> dict[str, Any]:
    """The whole waterfall for one line: where the list price came from, every discount
    with its amount, the margin and the floor it was checked against."""
    notes: list[str] = []
    problems: list[str] = []
    steps: list[dict[str, Any]] = []
    policy = book.policy_for(ln.family_id)
    family = book.families.get(ln.family_id) if ln.family_id else None
    unit_cost = ln.unit_cost if ln.cost_status in GOOD_COST else None
    source, price = "none", None

    found = book.erp_price(ln, notes) if ln.item_code else None
    if found is not None:
        try:
            rate = found.rate * units.factor(ln.unit, found.uom) if found.uom else found.rate
        except units.UnitError as e:
            problems.append(f"{found.connection}'s price is per {found.uom} and the line is "
                            f"in {ln.unit or 'no unit'}: {e}")
            rate = None
        if rate is not None:
            source, price = found.source, _round(rate, PAISA)
            where = ("a price for this customer" if found.source == "contract"
                     else f"the price list {found.price_list}")
            until = (f", valid until {found.valid_upto.isoformat()}"
                     if found.valid_upto else "")
            steps.append({"kind": "list", "label": f"From {found.connection}: {where}{until}",
                          "price": money(rate)})
            if found.synced_at is not None:
                notes.append(f"The price came from {found.connection}, last synced "
                             f"{found.synced_at.strftime('%d %b %Y %H:%M UTC')}.")
    if price is None:
        if unit_cost is not None and policy is not None and policy.target_margin_pct < 100:
            source = "cost_plus"
            price = _round(unit_cost / (1 - policy.target_margin_pct / 100), PAISA)
            steps.append({"kind": "list",
                          "label": f"{rs(unit_cost)} cost at the "
                                   f"{pct(policy.target_margin_pct)}% target margin"
                                   f"{f' for {family}' if family else ''}",
                          "price": money(price)})
        elif unit_cost is None:
            problems.append("The cost of this line is not known, and no price in an ERP "
                            "covers it, so Forge cannot price it.")
        elif policy is None:
            problems.append(
                "No pricing policy is set"
                + (f" for {family}" if family else "")
                + " or for the company, so Forge has no target margin to price the cost at "
                  "(Quotes → Pricing policy).")

    list_price = price
    if price is not None and source == "contract" and policy is not None \
            and (policy.tier_discount(book.tier) or policy.qty_break(ln.qty)):
        # The customer's own price is already negotiated: no tier or quantity discount on
        # top of it (Y6). The rep's ask still applies.
        notes.append("This customer's own price is used as it is: the tier discount and "
                     "quantity breaks are not taken off it.")
    if price is not None and policy is not None and source != "contract":
        tier_pct = policy.tier_discount(book.tier)
        if tier_pct:
            price, cut = _off(price, tier_pct)
            steps.append({"kind": "discount", "pct": pct(tier_pct),
                          "label": f"{book.tier or 'standard'} customer discount "
                                   f"{pct(tier_pct)}%",
                          "amount": money(-cut), "price": money(price)})
        band = policy.qty_break(ln.qty)
        if band is not None and band[1]:
            price, cut = _off(price, band[1])
            steps.append({"kind": "discount", "pct": pct(band[1]),
                          "label": f"Quantity break from {format(band[0].normalize(), 'f')}"
                                   f" {ln.unit or ''}".rstrip() + f": {pct(band[1])}%",
                          "amount": money(-cut), "price": money(price)})
    ask = ln.ask or {}
    if (gone := ask.get("cleared")) and gone.get("unit_price"):
        notes.append(f"The rep's price of {rs(_dec(gone['unit_price']))} was cleared because "
                     f"{gone.get('why') or 'the line changed'}; ask again if it still stands.")
    if price is not None and ask:
        if (want := _dec(ask.get("unit_price"))) is not None:
            want = _round(want, PAISA)
            steps.append({"kind": "ask", "label": f"The rep's price: {rs(want)}",
                          "amount": money(want - price), "price": money(want)})
            price = want
        elif (cut_pct := _dec(ask.get("discount_pct"))) is not None:
            price, cut = _off(price, cut_pct)
            steps.append({"kind": "ask", "pct": pct(cut_pct),
                          "label": f"The rep asked for {pct(cut_pct)}% off",
                          "amount": money(-cut), "price": money(price)})
    if price is not None:
        # Exact to the paisa unless a person switched rounding on for this quote (Y5).
        rounding = policy.rounding if (policy and book.round_off) else "0.01"
        rounded = _round(price, rounding)
        if rounded != price and rounding != "0.01":  # to the paisa is not worth a line
            steps.append({"kind": "rounding",
                          "label": f"Rounded up to the next ₹{rounding}",
                          "amount": money(rounded - price), "price": money(rounded)})
        price = rounded

    floor_pct = policy.floor_margin_pct if policy else None
    margin = _margin(price, unit_cost) if (price and unit_cost is not None and price > 0) \
        else None
    if price is None:
        status = "no_price"
    elif unit_cost is None:
        status = "no_cost"
        problems.append("The cost of this line is not known, so its margin cannot be "
                        "checked against the floor.")
    elif margin is None:
        status = "below_floor"
        problems.append("The price is zero, so the line is below any floor.")
    elif floor_pct is None:
        status = "no_floor"
        problems.append("No pricing policy is set, so there is no floor to check this "
                        "line against (Quotes → Pricing policy).")
    elif margin < floor_pct:
        status = "below_floor"
        problems.append(f"{pct(margin)}% margin is below the {pct(floor_pct)}% floor"
                        f"{f' for {family}' if family else ''}: "
                        f"{rs(unit_cost / (1 - floor_pct / 100))} would meet it.")
    else:
        status = "ok"

    qty = ln.qty
    out: dict[str, Any] = {
        "status": status, "source": source, "currency": book.currency,
        "list_price": money(list_price), "unit_price": money(price),
        "qty": None if qty is None else format(qty.normalize(), "f"), "unit": ln.unit,
        "amount": money(price * qty) if (price is not None and qty is not None) else None,
        "unit_cost": money(unit_cost), "margin_pct": pct(margin),
        "floor_margin_pct": pct(floor_pct), "tier": book.tier, "round_off": book.round_off,
        "policy": policy.out() if policy else None,
        "at": book.day.isoformat(), "steps": steps, "notes": notes, "problems": problems,
    }
    out["review_key"] = review_key(out)
    out["seal"] = seal_of(out)
    return out


def review_key(out: dict[str, Any]) -> str:
    """What a person approves on a price card: the price, the margin against the floor
    and why it is being asked. A changed price asks again; the same price does not."""
    content = {k: out.get(k) for k in ("status", "source", "unit_price", "margin_pct",
                                       "floor_margin_pct", "problems")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:32]


def seal_of(out: dict[str, Any]) -> str:
    """The fingerprint of a worked-out price: every step, rate and number that made it."""
    body = {k: v for k, v in out.items() if k not in ("seal", "approval")}
    return hashlib.sha256(chain.canonical(json.loads(json.dumps(body, default=str)))
                          .encode()).hexdigest()


# A revision --------------------------------------------------------------------------------

def totals(lines: list[QuoteLine]) -> dict[str, Any]:
    """The quote as a whole: what it comes to, and whether it clears the floor its own
    lines weigh out to (C2 A: the floor is checked on the whole quote too). Lines with
    no quantity, no price or no cost are left out of the margin and counted instead."""
    total = Decimal(0)
    cost = Decimal(0)
    floor_weight = Decimal(0)
    weighed = Decimal(0)
    left_out = 0
    for ln in lines:
        price, qty = ln.unit_price, ln.qty
        if price is None or qty is None:
            left_out += 1
            continue
        amount = price * qty
        total += amount
        p = ln.price or {}
        unit_cost, floor = _dec(p.get("unit_cost")), _dec(p.get("floor_margin_pct"))
        if unit_cost is None or floor is None:
            left_out += 1
            continue
        cost += unit_cost * qty
        weighed += amount
        floor_weight += floor * amount
    margin = _margin(weighed, cost) if weighed > 0 else None
    floor = (floor_weight / weighed) if weighed > 0 else None
    status = "ok"
    if margin is not None and floor is not None and margin < floor:
        status = "below_floor"
    return {"total": money(total), "cost": money(cost) if weighed > 0 else None,
            "margin_pct": pct(margin), "floor_margin_pct": pct(floor),
            "status": status, "lines_left_out": left_out}


def approved(ln: QuoteLine) -> bool:
    """A person approved the price this line has now (the card's key is the line's)."""
    p = ln.price or {}
    key = (p.get("approval") or {}).get("key")
    return bool(key) and key == p.get("review_key")


def revision_status(lines: list[QuoteLine]) -> str:
    """below_floor while any line waits for a price approval or the whole quote is under
    the floor its lines weigh out to; ok otherwise. Worked out after pricing and after
    every price card decided, so the two can never disagree."""
    waiting = any(ln.price_status in NEEDS_APPROVAL and not approved(ln) for ln in lines)
    return "below_floor" if waiting or totals(lines)["status"] == "below_floor" else "ok"


async def price_revision(session: AsyncSession, q: Quote, rev: QuoteRevision, actor: str,
                         why: str = "priced") -> dict[str, Any]:
    """Price every line of a draft revision and keep each waterfall on its line. A line
    below its floor, with no cost, with no floor or that nothing could price goes to a
    person who approves prices (C2 A); the full matrix of who approves what is F3.4."""
    from app.agents import runtime
    from app.cpq import price_agent

    quotes.must_be_draft(q, rev)
    lines = await quotes.lines_of(session, rev)
    book = await Book.load(session, q, lines, await quotes.today(session, q.tenant_id))
    book.round_off = rev.round_off
    approve: list[QuoteLine] = []
    summary: list[dict[str, Any]] = []
    line_seals: dict[str, str] = {}
    for ln in lines:
        out = price_line(book, ln)
        needs = out["status"] in NEEDS_APPROVAL
        before = (ln.price or {}).get("approval")
        # The same thing a person already approved is not asked again (K10 A's rule for
        # costs). A countered price is the one the line takes.
        settled = bool(needs and before and before.get("key") == out["review_key"])
        if settled:
            out["approval"] = before
        price = _dec((before or {}).get("unit_price")) if settled else None
        if price is None:
            price = _dec(out["unit_price"])
        unit_cost = _dec(out["unit_cost"])
        margin = _margin(price, unit_cost) if (
            price is not None and price > 0 and unit_cost is not None) else None
        ln.price, ln.price_source, ln.price_status = out, out["source"], out["status"]
        ln.list_price, ln.unit_price = _dec(out["list_price"]), price
        ln.margin_pct = margin if margin is None else _dec(pct(margin))
        ln.updated_at = datetime.now(UTC)
        if needs and not settled:
            approve.append(ln)
        elif ln.price_review_id is not None and not needs:
            await runtime.supersede(session, f"price:{ln.id}", actor,
                                    "The line clears its floor now")
            ln.price_review_id = None
        summary.append({"line": ln.position, "source": out["source"],
                        "status": out["status"], "approved": settled,
                        "unit_price": money(price), "margin_pct": pct(margin),
                        "floor_margin_pct": out["floor_margin_pct"]})
        line_seals[str(ln.position)] = out["seal"]
    whole = totals(lines)
    rev.priced_at = datetime.now(UTC)
    rev.price_status = revision_status(lines)
    await session.flush()
    await chain.append(session, q.tenant_id, "quote.priced", actor=actor,
                       record_type="quote", record_id=q.id, account_id=q.account_id,
                       evidence_class="customer_money",
                       data={"number": q.number, "revision": rev.number, "why": why,
                             "at": book.day.isoformat(), "tier": book.tier,
                             "round_off": book.round_off, "lines": summary, "totals": whole,
                             # Self-contained and in the chain's plain skeleton, so the
                             # seals stay readable once the rest is trimmed (R5).
                             "price_seals": {"revision": rev.number, "lines": line_seals}})
    if approve:
        await price_agent.ask_approval(session, q, rev, approve)
    return {"lines": summary, "totals": whole, "status": rev.price_status}


async def seals(session: AsyncSession, q: Quote, rev: QuoteRevision) -> dict[str, Any]:
    """Check each priced line against the fingerprint the audit chain holds for it, the
    way the cost seals do (F3.2): a waterfall changed in the database outside Forge no
    longer matches the seal in the audit record of the pricing."""
    rec = await session.scalar(
        select(AuditRecord).where(AuditRecord.tenant_id == q.tenant_id,
                                  AuditRecord.kind == "quote.priced",
                                  AuditRecord.record_type == "quote",
                                  AuditRecord.record_id == q.id)
        .order_by(AuditRecord.seq.desc()).limit(1))
    sealed = ((rec.data or {}).get("price_seals") or {}) if rec is not None else {}
    held = sealed.get("lines") or {}
    same_rev = sealed.get("revision") == rev.number
    lines = []
    for ln in await quotes.lines_of(session, rev):
        price = ln.price or {}
        if not price:
            lines.append({"line": ln.position, "state": "not_priced"})
            continue
        want = held.get(str(ln.position)) if same_rev else None
        if want is None:
            lines.append({"line": ln.position, "state": "not_sealed"})
            continue
        # Worked out again from the kept waterfall, never read from its own "seal" key.
        lines.append({"line": ln.position,
                      "state": "sealed" if seal_of(price) == want else "changed"})
    return {"revision": rev.number,
            "checked": {"seq": rec.seq, "at": rec.created_at.isoformat()}
            if rec is not None and same_rev else None,
            "ok": all(x["state"] != "changed" for x in lines), "lines": lines}


async def reprice_open_drafts(session: AsyncSession, tenant_id: uuid.UUID, actor: str,
                              why: str) -> list[str]:
    """Every open draft priced again, after the numbers behind prices change. Sent
    revisions are never touched: they are what the customer was given."""
    from app.models.quotes import OPEN_QUOTE_STATUSES

    rows = list(await session.execute(
        select(Quote, QuoteRevision).join(
            QuoteRevision, QuoteRevision.quote_id == Quote.id)
        .where(Quote.tenant_id == tenant_id, Quote.status.in_(OPEN_QUOTE_STATUSES),
               QuoteRevision.number == Quote.active_revision,
               QuoteRevision.status == "draft")
        .order_by(Quote.created_at.desc()).limit(200)))
    done = []
    for q, rev in rows:
        await price_revision(session, q, rev, actor, why=why)
        done.append(q.number)
    return done
