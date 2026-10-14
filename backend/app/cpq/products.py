"""Product families' options models and configurations in the database (F3.1, C4 A).

- save(): a new version of a family's model, only after app/cpq/configurator.py finds
  no conflicts or dead ends. Configurations on open enquiries that the new rules no
  longer allow (or that get a different variant code) are marked "configure again".
- configure(): a finished configuration, refused with the rule's reason if invalid.
- items_changed(): an ERP item an option points to changed after a configuration was
  made, so the configuration is marked "reprice recommended" (the ECN flag; quotes
  pick the flag up when they arrive, F3.7).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import chain
from app.cpq.configurator import Model, ModelProblems, next_rule_ids
from app.cpq.samples import SAMPLES
from app.models import Configuration, Enquiry, EnquiryLine, ErpItem, ProductFamily, ProductModel
from app.models.enquiries import OPEN_STATUSES

_cache: dict[uuid.UUID, Model] = {}


def model_of(row: ProductModel) -> Model:
    """The checker for a saved version (versions never change, so it is cached)."""
    m = _cache.get(row.id)
    if m is None:
        m = Model.parse({"attributes": row.attributes, "rules": row.rules,
                         "code_pattern": row.code_pattern})
        if len(_cache) > 500:
            _cache.clear()
        _cache[row.id] = m
    return m


async def current(session: AsyncSession, family_id: uuid.UUID) -> ProductModel | None:
    return await session.scalar(select(ProductModel).where(
        ProductModel.family_id == family_id, ProductModel.current.is_(True)))


async def with_models(session: AsyncSession) -> dict[uuid.UUID, ProductModel]:
    """Family id -> its current model, for every family that has one."""
    rows = await session.scalars(select(ProductModel).where(ProductModel.current.is_(True)))
    return {r.family_id: r for r in rows}


def check(data: dict[str, Any]) -> tuple[Model | None, list[str]]:
    """Parse and check a model as typed on the setup screen."""
    data = {**data, "rules": next_rule_ids([dict(r) for r in data.get("rules") or []
                                            if isinstance(r, dict)])}
    try:
        m = Model.parse(data)
    except ModelProblems as exc:
        return None, exc.problems
    return m, m.check()


async def save(session: AsyncSession, family: ProductFamily, data: dict[str, Any],
               actor: str, note: str | None = None) -> ProductModel:
    m, problems = check(data)
    if problems or m is None:
        raise ModelProblems(problems)
    clean = m.dump()
    before = await session.scalar(select(ProductModel).where(
        ProductModel.family_id == family.id, ProductModel.current.is_(True))
        .with_for_update())
    version = (await session.scalar(select(func.max(ProductModel.version)).where(
        ProductModel.family_id == family.id)) or 0) + 1
    if before is not None:
        before.current = False
        await session.flush()
    row = ProductModel(tenant_id=family.tenant_id, family_id=family.id, version=version,
                       current=True, note=(note or "").strip()[:300] or None,
                       created_by=actor, **clean)
    session.add(row)
    await session.flush()
    await chain.append(session, family.tenant_id, "product.options.saved", actor=actor,
                       record_type="product_family", record_id=family.id,
                       data={"family": family.name, "version": version,
                             "attributes": len(clean["attributes"]),
                             "rules": len(clean["rules"]), "note": row.note})
    await _recheck(session, family, row, actor)
    return row


async def add_sample(session: AsyncSession, family: ProductFamily, actor: str) -> None:
    """A family added from a template that has a sample model gets it as version 1."""
    sample = SAMPLES.get(family.template or "")
    if sample and await current(session, family.id) is None:
        await save(session, family, sample, actor, note="Sample from the template")


async def _open_lines(session: AsyncSession, family_id: uuid.UUID
                      ) -> list[tuple[EnquiryLine, Configuration, Enquiry]]:
    rows = await session.execute(
        select(EnquiryLine, Configuration, Enquiry)
        .join(Configuration, EnquiryLine.configuration_id == Configuration.id)
        .join(Enquiry, EnquiryLine.enquiry_id == Enquiry.id)
        .where(Configuration.family_id == family_id, EnquiryLine.removed_at.is_(None),
               Enquiry.status.in_(OPEN_STATUSES)))
    return [(r[0], r[1], r[2]) for r in rows.all()]


async def _recheck(session: AsyncSession, family: ProductFamily, row: ProductModel,
                   actor: str) -> None:
    from app.enquiries import records

    m = model_of(row)
    now = datetime.now(UTC)
    for line, cfg, enq in await _open_lines(session, family.id):
        if cfg.model_id == row.id:
            continue
        try:
            got = m.configure(str(family.id), cfg.values)
        except ModelProblems as exc:
            why = (f"The options of {family.name} changed (version {row.version}) and no "
                   f"longer allow it: {exc.problems[0]}. Configure it again")
        else:
            if got.variant_code == cfg.variant_code:
                continue
            why = (f"The options of {family.name} changed (version {row.version}): its "
                   f"variant code is now {got.variant_code}. Configure it again")
        cfg.reprice_reason, cfg.reprice_at = why[:500], now
        await records.event(session, enq, "line_reprice", actor, line=line.position,
                            reason=cfg.reprice_reason)


async def configure(session: AsyncSession, family: ProductFamily, values: dict[str, Any],
                    actor: str) -> Configuration:
    row = await current(session, family.id)
    if row is None:
        raise ModelProblems([f"{family.name} has no options set up"])
    got = model_of(row).configure(str(family.id), values)
    cfg = Configuration(tenant_id=family.tenant_id, family_id=family.id, model_id=row.id,
                        values=got.values, labels=got.labels, variant_code=got.variant_code,
                        fingerprint=got.fingerprint, created_by=actor)
    session.add(cfg)
    await session.flush()
    return cfg


async def items_changed(session: AsyncSession, item_ids: list[uuid.UUID]) -> int:
    """ERP items that changed: configurations made before the change whose options
    point to them are marked "reprice recommended". Returns how many were marked."""
    from app.enquiries import records

    if not item_ids:
        return 0
    items = {i.code: i for i in await session.scalars(
        select(ErpItem).where(ErpItem.id.in_(item_ids)))}
    if not items:
        return 0
    marked = 0
    for row in (await with_models(session)).values():
        m = model_of(row)
        hit = m.items() & items.keys()
        if not hit:
            continue
        # option value -> item code, per attribute
        points = {(a.key, o.value): o.erp_item for a in m.attributes for o in a.options
                  if o.erp_item in hit}
        cfgs = await session.scalars(select(Configuration).where(
            Configuration.family_id == row.family_id))
        for cfg in cfgs:
            codes = {points[(k, v)] for k, v in cfg.values.items() if (k, v) in points}
            changed = [c for c in codes if items[c].modified_at > cfg.created_at]
            if not changed:
                continue
            item = items[changed[0]]
            cfg.reprice_reason = (f"ERP item {item.code} ({item.name}) changed on "
                                  f"{item.modified_at:%d %b %Y}: reprice recommended")[:500]
            cfg.reprice_at = datetime.now(UTC)
            marked += 1
            lines = await session.execute(
                select(EnquiryLine, Enquiry).join(Enquiry, EnquiryLine.enquiry_id == Enquiry.id)
                .where(EnquiryLine.configuration_id == cfg.id,
                       EnquiryLine.removed_at.is_(None), Enquiry.status.in_(OPEN_STATUSES)))
            for line, enq in lines.all():
                await records.event(session, enq, "line_reprice", "system:forge",
                                    line=line.position, reason=cfg.reprice_reason)
    return marked


async def link(session: AsyncSession, line: EnquiryLine, cfg: Configuration) -> None:
    line.configuration_id = cfg.id
    line.updated_at = datetime.now(UTC)
    await session.flush()


def model_out(row: ProductModel | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": str(row.id), "version": row.version, "attributes": row.attributes,
            "rules": row.rules, "code_pattern": row.code_pattern, "note": row.note,
            "created_by": row.created_by, "created_at": row.created_at}


def config_out(c: Configuration | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {"id": str(c.id), "family_id": str(c.family_id), "model_id": str(c.model_id),
            "values": c.values, "labels": c.labels, "variant_code": c.variant_code,
            "fingerprint": c.fingerprint, "reprice_reason": c.reprice_reason,
            "reprice_at": c.reprice_at, "created_by": c.created_by, "created_at": c.created_at}
