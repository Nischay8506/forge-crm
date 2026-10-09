"""The mapping studio's code lists (decision V3, W1 A).

Each ERP's codes (units, customer groups, territories, payment terms, warehouses,
tax templates) map onto Forge's own lists, which Phase 2 quotes and orders use:
- units: the GST unit codes (UQC) used on e-invoices;
- tax: GST rate slabs;
- customer groups, territories, payment terms and warehouses: a copy of the first
  ERP's own lists (that ERP's codes map onto themselves, confirmed); codes that
  appear later in that ERP are added too. Every later ERP maps onto these.

New ERP codes are found after each sync and get a suggestion (same code or name,
a known unit alias, the rate in a tax template's name, or a close spelling). A
person confirms suggestions, one by one or many at once. AI suggestions join through
the LLM gateway once the D3 keys are set. Every change is a new version in
code_map_history and a record in the audit chain.
"""

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.core.errors import AppError, NotFound
from app.integrations.contracts import Entity
from app.models import (
    CodeMap,
    CodeMapHistory,
    ErpConnection,
    ErpCustomer,
    ErpInvoice,
    ErpItem,
    ErpOrder,
    ErpPrice,
    ErpStock,
    ForgeCode,
)

LISTS: dict[str, str] = {
    "uom": "Units of measure",
    "customer_group": "Customer groups",
    "territory": "Territories",
    "payment_terms": "Payment terms",
    "warehouse": "Warehouses",
    "tax": "Tax templates",
}
STANDARD_LISTS = frozenset({"uom", "tax"})

# GST Unit Quantity Codes (UQC) as used on GST returns and e-invoices.
UQC: dict[str, str] = {
    "BAG": "Bags", "BAL": "Bale", "BDL": "Bundles", "BKL": "Buckles", "BOU": "Billion of units",
    "BOX": "Box", "BTL": "Bottles", "BUN": "Bunches", "CAN": "Cans", "CBM": "Cubic meters",
    "CCM": "Cubic centimeters", "CMS": "Centimeters", "CTN": "Cartons", "DOZ": "Dozens",
    "DRM": "Drums", "GGK": "Great gross", "GMS": "Grammes", "GRS": "Gross", "GYD": "Gross yards",
    "KGS": "Kilograms", "KLR": "Kilolitre", "KME": "Kilometre", "LTR": "Litres",
    "MLT": "Millilitre", "MTR": "Meters", "MTS": "Metric ton", "NOS": "Numbers", "OTH": "Others",
    "PAC": "Packs", "PCS": "Pieces", "PRS": "Pairs", "QTL": "Quintal", "ROL": "Rolls",
    "SET": "Sets", "SQF": "Square feet", "SQM": "Square meters", "SQY": "Square yards",
    "TBS": "Tablets", "TGM": "Ten gross", "THD": "Thousands", "TON": "Tonnes", "TUB": "Tubes",
    "UGS": "US gallons", "UNT": "Units", "YDS": "Yards",
}
UOM_ALIASES: dict[str, str] = {
    "nos": "NOS", "no": "NOS", "number": "NOS", "numbers": "NOS", "unit": "UNT", "units": "UNT",
    "pc": "PCS", "pcs": "PCS", "piece": "PCS", "pieces": "PCS", "kg": "KGS", "kgs": "KGS",
    "kilogram": "KGS", "kilograms": "KGS", "g": "GMS", "gm": "GMS", "gram": "GMS",
    "grams": "GMS", "l": "LTR", "ltr": "LTR", "litre": "LTR", "liter": "LTR", "litres": "LTR",
    "ml": "MLT", "millilitre": "MLT", "m": "MTR", "meter": "MTR", "metre": "MTR",
    "meters": "MTR", "cm": "CMS", "km": "KME", "box": "BOX", "boxes": "BOX", "set": "SET",
    "sets": "SET", "pair": "PRS", "pairs": "PRS", "dozen": "DOZ", "doz": "DOZ", "roll": "ROL",
    "rolls": "ROL", "bag": "BAG", "bags": "BAG", "bottle": "BTL", "can": "CAN", "drum": "DRM",
    "carton": "CTN", "pack": "PAC", "packet": "PAC", "ton": "TON", "tonne": "TON",
    "metric ton": "MTS", "quintal": "QTL", "sq ft": "SQF", "square foot": "SQF",
    "sq m": "SQM", "square meter": "SQM", "cubic meter": "CBM", "tube": "TUB", "yard": "YDS",
}
# GST rate slabs, including the older 12% and 28% still found on templates and invoices.
GST_RATES = ("0", "0.1", "0.25", "1.5", "3", "5", "12", "18", "28", "40")
TAX: dict[str, str] = {
    **{f"GST_{r}": f"GST {r}%" for r in GST_RATES},
    "EXEMPT": "Exempt", "NIL": "Nil rated", "NON_GST": "Non-GST supply",
}
STANDARD: dict[str, dict[str, str]] = {"uom": UQC, "tax": TAX}

# Where each list's ERP codes are found in the synced records.
SOURCES: dict[str, list[tuple[Any, Any]]] = {
    "uom": [(ErpItem, ErpItem.uom), (ErpPrice, ErpPrice.uom), (ErpStock, ErpStock.uom)],
    "customer_group": [(ErpCustomer, ErpCustomer.customer_group)],
    "territory": [(ErpCustomer, ErpCustomer.territory)],
    "payment_terms": [(ErpCustomer, ErpCustomer.payment_terms)],
    "warehouse": [(ErpStock, ErpStock.warehouse)],
    "tax": [(ErpOrder, ErpOrder.data["tax_template"].astext),
            (ErpInvoice, ErpInvoice.data["tax_template"].astext)],
}
LISTS_BY_ENTITY: dict[Entity, tuple[str, ...]] = {
    Entity.customer: ("customer_group", "territory", "payment_terms"),
    Entity.item: ("uom",), Entity.price: ("uom",), Entity.stock: ("uom", "warehouse"),
    Entity.order: ("tax",), Entity.invoice: ("tax",), Entity.receipt: (),
}
SYSTEM = "system:mapping"


@dataclass
class Suggestion:
    code: str
    source: str  # seed | exact | alias | rate | similar
    confidence: float


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%.]+", " ", s.lower()).strip()


async def ensure_standard(session: AsyncSession, tenant_id: uuid.UUID, list_: str) -> None:
    if list_ not in STANDARD:
        return
    have = await session.scalar(select(func.count()).select_from(ForgeCode).where(
        ForgeCode.list == list_, ForgeCode.source == "standard"))
    if have:
        return
    stmt = pg_insert(ForgeCode).values([
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "list": list_, "code": code,
         "label": label, "source": "standard", "active": True}
        for code, label in STANDARD[list_].items()]).on_conflict_do_nothing()
    await session.execute(stmt)


def suggest(list_: str, erp_code: str, codes: dict[str, str]) -> Suggestion | None:
    """The best Forge code for an ERP code, or None. `codes` is code -> label."""
    if not codes:
        return None
    n = _norm(erp_code)
    for code, label in codes.items():
        if n in (_norm(code), _norm(label)):
            return Suggestion(code, "exact", 100.0)
    if list_ == "uom":
        alias = UOM_ALIASES.get(n) or UOM_ALIASES.get(n.rstrip("s"))
        if alias and alias in codes:
            return Suggestion(alias, "alias", 95.0)
    if list_ == "tax":
        lowered = n.replace(" ", "")
        if "exempt" in lowered:
            return Suggestion("EXEMPT", "rate", 90.0) if "EXEMPT" in codes else None
        if "nil" in lowered:
            return Suggestion("NIL", "rate", 90.0) if "NIL" in codes else None
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", erp_code)
        if m:
            rate = str(Decimal(m.group(1)).normalize())
            if f"GST_{rate}" in codes:
                return Suggestion(f"GST_{rate}", "rate", 90.0)
    best, score = None, 0.0
    for code, label in codes.items():
        s = max(fuzz.token_sort_ratio(n, _norm(label)), fuzz.ratio(n, _norm(code)))
        if s > score:
            best, score = code, s
    if best and score >= 85:
        return Suggestion(best, "similar", round(score, 2))
    return None


async def _seed_connection(session: AsyncSession, list_: str) -> uuid.UUID | None:
    """The connection whose codes started a copied list (the first ERP)."""
    src = await session.scalar(select(ForgeCode.source).where(
        ForgeCode.list == list_, ForgeCode.source.like("erp:%"))
        .order_by(ForgeCode.created_at).limit(1))
    return uuid.UUID(src.removeprefix("erp:")) if src else None


async def _forge_codes(session: AsyncSession, list_: str) -> dict[str, str]:
    rows = await session.execute(select(ForgeCode.code, ForgeCode.label).where(
        ForgeCode.list == list_, ForgeCode.active.is_(True)))
    return {r.code: r.label for r in rows}


async def _history(session: AsyncSession, m: CodeMap, actor: str) -> None:
    session.add(CodeMapHistory(id=uuid.uuid4(), tenant_id=m.tenant_id, code_map_id=m.id,
                               version=m.version, forge_code=m.forge_code, status=m.status,
                               suggestion_source=m.suggestion_source, changed_by=actor))


async def discover(session: AsyncSession, conn: ErpConnection, entity: Entity) -> dict[str, int]:
    """Add codes seen in this connection's synced records that the studio does not
    have yet, with a suggestion (or a confirmed copy for the first ERP's lists)."""
    added: dict[str, int] = {}
    for list_ in LISTS_BY_ENTITY.get(entity, ()):
        values: set[str] = set()
        for model, column in SOURCES[list_]:
            rows = await session.scalars(select(column).distinct().where(
                model.connection_id == conn.id, column.is_not(None), column != ""))
            values.update(v.strip() for v in rows if v and v.strip())
        known = set(await session.scalars(select(CodeMap.erp_code).where(
            CodeMap.connection_id == conn.id, CodeMap.list == list_)))
        new = sorted(values - known)
        if not new:
            continue
        await ensure_standard(session, conn.tenant_id, list_)
        if list_ not in STANDARD:
            seed = await _seed_connection(session, list_)
            if seed is None or seed == conn.id:
                # W1 A: the first ERP's own list becomes Forge's list.
                await session.execute(pg_insert(ForgeCode).values([
                    {"id": uuid.uuid4(), "tenant_id": conn.tenant_id, "list": list_, "code": v,
                     "label": v, "source": f"erp:{conn.id}", "active": True} for v in new]
                ).on_conflict_do_nothing())
        codes = await _forge_codes(session, list_)
        seed = None if list_ in STANDARD else await _seed_connection(session, list_)
        for v in new:
            if seed == conn.id and v in codes:
                s = Suggestion(v, "seed", 100.0)
                status = "confirmed"
            else:
                s = suggest(list_, v, codes)
                status = "suggested" if s else "unmapped"
            m = CodeMap(id=uuid.uuid4(), tenant_id=conn.tenant_id, connection_id=conn.id,
                        list=list_, erp_code=v, forge_code=s.code if s else None, status=status,
                        suggestion_source=s.source if s else None,
                        confidence=Decimal(str(s.confidence)) if s else None, version=1,
                        updated_by=SYSTEM)
            session.add(m)
            await session.flush()
            await _history(session, m, SYSTEM)
        added[list_] = len(new)
        await chain.append(session, conn.tenant_id, "mapping.codes_found", actor=SYSTEM,
                           record_type="erp_connection", record_id=conn.id,
                           data={"list": list_, "connection": conn.name, "codes": new[:50],
                                 "count": len(new)})
    return added


async def change(session: AsyncSession, m: CodeMap, *, forge_code: str | None, actor: str,
                 note: str | None = None) -> CodeMap:
    """A person confirms, changes or clears one mapping (a new version, audited)."""
    if forge_code is not None:
        exists = await session.scalar(select(ForgeCode.id).where(
            ForgeCode.list == m.list, ForgeCode.code == forge_code, ForgeCode.active.is_(True)))
        if exists is None:
            raise AppError(f"{forge_code!r} is not in Forge's {LISTS[m.list].lower()} list")
    status = "confirmed" if forge_code else "unmapped"
    if m.forge_code == forge_code and m.status == status:
        return m
    before = {"forge_code": m.forge_code, "status": m.status, "version": m.version}
    m.forge_code, m.status, m.version, m.updated_by = forge_code, status, m.version + 1, actor
    if forge_code != before["forge_code"]:
        m.suggestion_source, m.confidence = "manual", None
    await session.flush()
    await _history(session, m, actor)
    await chain.append(session, m.tenant_id, "mapping.changed", actor=actor,
                       record_type="erp_connection", record_id=m.connection_id,
                       data={"code_map_id": m.id, "list": m.list, "erp_code": m.erp_code,
                             "before": before, "after": {"forge_code": forge_code,
                                                         "status": status,
                                                         "version": m.version},
                             "note": note})
    return m


async def confirm_many(session: AsyncSession, ids: list[uuid.UUID], actor: str) -> int:
    """Confirm several suggestions at once (each still gets its own version)."""
    rows = list(await session.scalars(select(CodeMap).where(CodeMap.id.in_(ids))))
    if len(rows) != len(set(ids)):
        raise NotFound("Some mappings were not found")
    n = 0
    for m in rows:
        if m.status == "suggested" and m.forge_code:
            await change(session, m, forge_code=m.forge_code, actor=actor, note="bulk confirm")
            n += 1
    return n


async def add_forge_code(session: AsyncSession, tenant_id: uuid.UUID, list_: str, code: str,
                         label: str, actor: str) -> ForgeCode:
    if list_ not in LISTS:
        raise AppError(f"Unknown list {list_!r}")
    if list_ in STANDARD_LISTS:
        raise AppError(f"{LISTS[list_]} use the standard GST list; map to one of its codes")
    code, label = code.strip(), (label or code).strip()
    if not code:
        raise AppError("Code is required")
    exists = await session.scalar(select(ForgeCode).where(ForgeCode.list == list_,
                                                          ForgeCode.code == code))
    if exists is not None:
        raise AppError(f"{code!r} is already in the list")
    fc = ForgeCode(id=uuid.uuid4(), tenant_id=tenant_id, list=list_, code=code, label=label,
                   source="manual", active=True)
    session.add(fc)
    await session.flush()
    await chain.append(session, tenant_id, "mapping.code_added", actor=actor,
                       data={"list": list_, "code": code, "label": label})
    return fc
