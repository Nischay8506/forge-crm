"""Lead scoring v1 (F1.7, decision N15 A): three grades a person understands, Fit,
Intent and Risk, combined into one priority from 0 to 100 that orders the Enquiry
Queue, with the top three reasons in words.

  Fit     can we make it (F1.6), is the order the size we like (the family's best
          order size), is the customer in an industry we want
  Intent  how formal the request is (tender, drawing, item numbers, a date), how
          soon it is needed, how senior the person asking is
  Risk    overdue money in the ERP (F12.6), an export or a watched country, a new
          or unknown customer

Each signal scores 0 to 100 and has a weight the company can change (Enquiries →
Setup → Scoring). Priority = the geometric mean of Fit and Intent, lowered by Risk
(by up to the risk weight). No learned model and no hidden numbers: v2 learns from
won and lost deals once M2 and M3 exist.

The score is worked out again on every new signal (a new enquiry, its lines
changing, the check, a reply, the customer being added, and hourly for ERP money),
and the owner is told when it jumps by the set number of points.
"""

import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enquiries import capability, records
from app.enquiries import settings as settings_mod
from app.models import Account, Enquiry, EnquiryLine, InboundEmail, ProductFamily
from app.models.enquiries import OPEN_STATUSES

SIGNALS = {
    "fit": {"make": "Can we make it", "volume": "Order size", "industry": "Industry"},
    "intent": {"formality": "How formal the request is", "timeline": "How soon it is needed",
               "seniority": "Who is asking"},
    "risk": {"receivables": "Overdue money in the ERP", "geography": "Export or watched country",
             "new_entity": "New or unknown customer"},
}
MAKE_POINTS = {"standard": 100, "cto": 80, "eto": 55, "no_fit": 0, None: 60}
FREE_MAIL = ("gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com",
             "rediffmail.com", "ymail.com", "live.com", "icloud.com", "aol.com")
SENIOR = ("director", "owner", "proprietor", "partner", "ceo", "md", "managing", "president",
          "vp", "vice president", "gm", "general manager", "head", "chief")
MIDDLE = ("manager", "lead", "sr", "senior", "incharge", "in-charge", "agm", "dgm")
JUNIOR = ("buyer", "officer", "executive", "engineer", "assistant", "coordinator")
URGENT = ("urgent", "immediate", "asap", "jaldi", "turant", "అత్యవసర", "तुरंत", "जल्दी")


def grade(part: str, points: int) -> str:
    if part == "risk":
        return "High" if points >= 60 else "Medium" if points >= 30 else "Low"
    return "High" if points >= 70 else "Medium" if points >= 40 else "Low"


def _lakh(v: Decimal) -> str:
    v = float(v)
    if v >= 1e7:
        return f"₹{v / 1e7:.1f} crore"
    if v >= 1e5:
        return f"₹{v / 1e5:.1f} lakh"
    return f"₹{v:,.0f}"


def _has(text: str, words: tuple[str, ...]) -> bool:
    low = f" {text.lower()} "
    return any(f" {w} " in low or f" {w}." in low or f" {w}," in low or w in low.split()
               for w in words)


async def _signals(session: AsyncSession, enq: Enquiry, cfg: dict[str, Any], now: datetime
                   ) -> dict[str, dict[str, tuple[int, str]]]:
    """{part: {signal: (points 0-100, why in words)}}."""
    from app.approvals.card import erp_totals

    lines = list(await session.scalars(select(EnquiryLine).where(
        EnquiryLine.enquiry_id == enq.id, EnquiryLine.removed_at.is_(None))))
    account = await session.get(Account, enq.account_id) if enq.account_id else None
    profile = await capability.load(session, enq.tenant_id)
    out: dict[str, dict[str, tuple[int, str]]] = {"fit": {}, "intent": {}, "risk": {}}

    # Fit ---------------------------------------------------------------------
    words = {"standard": "A standard product we make", "cto": "A configurable product we make",
             "eto": "Needs engineering before we can quote", "no_fit": "Looks like no fit",
             None: "Not checked yet"}
    out["fit"]["make"] = (MAKE_POINTS[enq.make_ability], words[enq.make_ability])
    fam_ids = {enq.product_family_id} if enq.product_family_id else set()
    vol: list[tuple[int, str]] = []
    for fid in fam_ids:
        f = await session.get(ProductFamily, fid)
        if f is None or not f.sweet_spot:
            continue
        unit = f.sweet_spot["unit"]
        total = sum(float(ln.qty) for ln in lines
                    if ln.qty is not None and (ln.unit or "").lower() == unit.lower())
        if not total:
            continue
        lo, hi = f.sweet_spot.get("min"), f.sweet_spot.get("max")
        size = f"{total:g} {unit}"
        if lo is not None and total < float(lo):
            vol.append((int(20 + 25 * total / float(lo)), f"{size} is smaller than the "
                        f"orders we like ({float(lo):g} {unit} and up)"))
        elif hi is not None and total > float(hi):
            vol.append((80, f"{size} is larger than our usual orders"))
        else:
            vol.append((100, f"{size} is the order size we like"))
    out["fit"]["volume"] = vol[0] if vol else (60, "Order size not known yet")
    industry = (account.industry or "").strip() if account else ""
    if not industry:
        out["fit"]["industry"] = (60, "Industry not known")
    elif profile and any(x.lower() in industry.lower() for x in profile.excluded_industries):
        out["fit"]["industry"] = (0, f"We don't supply the {industry} industry")
    elif profile and any(x.lower() in industry.lower() or industry.lower() in x.lower()
                         for x in profile.target_industries):
        out["fit"]["industry"] = (100, f"{industry}: an industry we want")
    else:
        out["fit"]["industry"] = (50, f"{industry}: not one of our target industries")

    # Intent ------------------------------------------------------------------
    emails = list(await session.scalars(select(InboundEmail).where(
        InboundEmail.enquiry_id == enq.id).limit(5)))
    text = "\n".join([enq.summary or "", *[(e.subject or "") + "\n" + (e.body_text or "")
                                          for e in emails]])
    files = [a for e in emails for a in (e.attachments or [])]
    drawing = any(str(a.get("filename", "")).lower().endswith(
        (".pdf", ".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")) for a in files) \
        or _has(text, ("drawing", "drg", "rev"))
    bits, pts = [], 20
    if enq.is_tender or enq.details.get("tender_ref"):
        pts += 35
        bits.append("a tender")
    if drawing:
        pts += 25
        bits.append("a drawing or specification")
    if any(ln.item_ref for ln in lines) or len(lines) >= 5:
        pts += 20
        bits.append("item numbers or a full list" if any(ln.item_ref for ln in lines)
                    else f"a list of {len(lines)} items")
    if enq.details.get("target_date") or enq.details.get("target_date_text"):
        pts += 20
        bits.append("a date")
    out["intent"]["formality"] = (min(100, pts), "A formal request: " + ", ".join(bits)
                                  if bits else "An informal request with few details")
    target = enq.details.get("target_date")
    try:
        when = date.fromisoformat(str(target)[:10]) if target else None
    except ValueError:
        when = None
    if when:
        days = (when - now.date()).days
        out["intent"]["timeline"] = (
            (100, f"Needed within {max(days, 0)} days") if days <= 30 else
            (70, f"Needed in about {round(days / 7)} weeks") if days <= 90 else
            (40, "Needed after three months"))
    elif _has(text, URGENT):
        out["intent"]["timeline"] = (90, "Marked urgent")
    else:
        out["intent"]["timeline"] = (40, "No date given")
    title = str(enq.customer.get("designation") or "").strip()
    out["intent"]["seniority"] = (
        (100, f"Asked by a {title}") if title and _has(title, SENIOR) else
        (80, f"Asked by a {title}") if title and _has(title, MIDDLE) else
        (60, f"Asked by a {title}") if title and _has(title, JUNIOR) else
        (50, f"Asked by a {title}") if title else (40, "Who is asking is not known"))

    # Risk --------------------------------------------------------------------
    if account is not None:
        erp = await erp_totals(session, account.id)
        if not erp.get("linked"):
            out["risk"]["receivables"] = (10, "Not linked to the ERP, so no payment history")
        else:
            inr = [r for r in erp["rows"] if r[0] == "INR"] or erp["rows"]
            overdue = sum((Decimal(r[2]) for r in inr), Decimal(0))
            outstanding = sum((Decimal(r[1]) for r in inr), Decimal(0))
            stale = " (ERP copy is old)" if erp.get("stale") else ""
            if overdue > 0:
                share = float(overdue / outstanding) if outstanding else 1.0
                out["risk"]["receivables"] = (int(40 + 60 * min(1.0, share)),
                                              f"{_lakh(overdue)} overdue in the ERP{stale}")
            else:
                out["risk"]["receivables"] = (0, f"Nothing overdue in the ERP{stale}")
    else:
        out["risk"]["receivables"] = (10, "No account, so no payment history")
    country = (account.country if account else None) or "IN"
    watch = [c.upper() for c in cfg.get("export_watch") or []]
    out["risk"]["geography"] = (
        (100, f"{country} is on our export-control watch list") if country.upper() in watch
        else (50, f"An export enquiry ({country})") if country.upper() != "IN"
        else (0, "In India"))
    new, why = 0, []
    if account is None:
        new, why = 60, ["a new customer, not in our accounts"]
    else:
        if account.created_at and now - account.created_at < timedelta(days=30):
            new += 40
            why.append("an account added in the last 30 days")
        if country.upper() == "IN" and not account.gstin:
            new += 20
            why.append("no GSTIN")
    domain = str(enq.customer.get("email") or "").rsplit("@", 1)[-1].lower()
    if domain in FREE_MAIL:
        new += 30
        why.append("writes from a free email address")
    out["risk"]["new_entity"] = (min(100, new), ("Risk: " + ", ".join(why)) if why
                                 else "A known customer")
    return out


def combine(signals: dict[str, dict[str, tuple[int, str]]], cfg: dict[str, Any]
            ) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    """(priority, parts, top three reasons)."""
    parts: dict[str, Any] = {}
    effects: list[tuple[float, str, bool]] = []
    for part, sigs in signals.items():
        weights = cfg[part]
        total = sum(max(0, int(weights.get(k, 0))) for k in sigs) or 1
        points = round(sum(p * max(0, int(weights.get(k, 0))) for k, (p, _) in sigs.items())
                       / total)
        parts[part] = {"points": points, "grade": grade(part, points), "signals": [
            {"key": k, "label": SIGNALS[part][k], "points": p, "weight": int(weights.get(k, 0)),
             "text": t} for k, (p, t) in sigs.items()]}
        for k, (p, t) in sigs.items():
            w = max(0, int(weights.get(k, 0))) / total
            if part == "risk":
                if p >= 30:
                    effects.append((-w * p / 100, t, False))
            else:
                effects.append((w * (p - 50) / 50, t, p >= 50))
    risk_cut = max(0, min(100, int(cfg.get("risk_weight", 50)))) / 100
    priority = math.sqrt(parts["fit"]["points"] * parts["intent"]["points"]) * \
        (1 - risk_cut * parts["risk"]["points"] / 100)
    top = sorted((e for e in effects if abs(e[0]) > 0.01), key=lambda e: -abs(e[0]))[:3]
    return max(0, min(100, round(priority))), parts, [{"text": t, "up": up}
                                                       for _, t, up in top]


async def rescore(session: AsyncSession, enq: Enquiry, *, actor: str = "system:forge",
                  why: str, now: datetime | None = None, notify: bool = True) -> int | None:
    """Work the score out again. Tells the owner when it jumps."""
    from app.enquiries.routing import notify_people

    if enq.status not in OPEN_STATUSES:
        return enq.score
    now = now or datetime.now(UTC)
    cfg = (await settings_mod.load(session, enq.tenant_id))["scoring"]
    score, parts, top = combine(await _signals(session, enq, cfg, now), cfg)
    before = enq.score
    enq.score, enq.score_parts, enq.score_reasons, enq.scored_at = score, parts, top, now
    if before != score:
        await records.event(session, enq, "scored", actor, score=score, before=before,
                            why=why, fit=parts["fit"]["grade"], intent=parts["intent"]["grade"],
                            risk=parts["risk"]["grade"])
    jump = int(cfg.get("jump", 20))
    if notify and before is not None and abs(score - before) >= jump and enq.owner_id:
        word = "up" if score > before else "down"
        await notify_people(session, enq, {enq.owner_id},
                            f"{enq.ref}: priority {word} from {before} to {score}",
                            top[0]["text"] if top else why)
    return score


async def rescore_open(session: AsyncSession, now: datetime | None = None,
                       notify: bool = True) -> int:
    """The hourly pass (ERP money and dates change without a new signal), and after the
    weights change (then without telling owners)."""
    n = 0
    for enq in await session.scalars(select(Enquiry).where(
            Enquiry.status.in_(OPEN_STATUSES), Enquiry.merged_into_id.is_(None))):
        before = enq.score
        if await rescore(session, enq, why="hourly" if notify else "new weights", now=now,
                         notify=notify) != before:
            n += 1
    return n


def view(enq: Enquiry) -> dict[str, Any]:
    return {"score": enq.score, "parts": enq.score_parts, "reasons": enq.score_reasons,
            "scored_at": enq.scored_at}
