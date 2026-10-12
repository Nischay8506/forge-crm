"""The company's capability profile (F1.6, decision N8 A): what it can make, set up on
Enquiries → Setup → What we make.

  processes            what the shop can do, each with its limits (the largest
                       diameter, length, thickness or piece weight) and the product
                       families it applies to
  materials            the grades and standards it works with
  certifications       the approvals it holds (an enquiry asking for one it does not
                       hold needs engineering)
  excluded_industries  industries it does not supply (an enquiry from one is no fit)
  target_industries    industries it wants more of (lead scoring, F1.7)
  engineering_role_id  the role that reviews enquiries needing engineering (P2 A)

Together with each product family's usual way of supplying it (standard,
configurable, needs engineering or not made) and its minimum order, this is what the
check in app/enquiries/qualify.py reads.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapabilityProfile, Role

LIMITS = {"max_diameter_mm": "Largest diameter (mm)", "max_length_mm": "Longest length (mm)",
          "max_thickness_mm": "Thickest (mm)", "max_weight_kg": "Heaviest piece (kg)"}
LISTS = ("materials", "certifications", "excluded_industries", "target_industries")

# A sample steel and fabrication company (N8 A), to start from or to test with. Each
# process names the product family templates it applies to, so Setup can link it to
# the company's own families made from those templates.
EXAMPLE: dict[str, Any] = {
    "processes": [
        {"name": "Plate cutting (CNC plasma and oxy-fuel)", "max_thickness_mm": 100,
         "max_length_mm": 12000, "tolerance": "±1 mm", "family_templates": ["steel"]},
        {"name": "Fabrication and welding", "max_weight_kg": 15000, "tolerance": None,
         "family_templates": ["fabrication"]},
        {"name": "CNC turning", "max_diameter_mm": 300, "max_length_mm": 1500,
         "tolerance": "IT7", "family_templates": ["machined"]},
    ],
    "materials": ["IS 2062 E250", "IS 2062 E350", "IS 3502", "IS 277", "Fe 500D", "EN8",
                  "EN24", "SS 304", "SS 316", "ASTM A240", "ASTM A36"],
    "certifications": ["ISO 9001:2015", "ISO 3834-2"],
    "excluded_industries": ["Defence", "Nuclear"],
    "target_industries": ["Infrastructure", "Power", "Cement", "Steel plants",
                          "Pre-engineered buildings"],
}


async def load(session: AsyncSession, tenant_id: uuid.UUID) -> CapabilityProfile | None:
    return await session.get(CapabilityProfile, tenant_id)


def out(p: CapabilityProfile | None, role_name: str | None = None) -> dict[str, Any]:
    if p is None:
        return {"processes": [], **{k: [] for k in LISTS}, "engineering_role_id": None,
                "engineering_role": None, "is_set_up": False, "updated_by": None,
                "updated_at": None}
    return {"processes": p.processes, **{k: getattr(p, k) for k in LISTS},
            "engineering_role_id": str(p.engineering_role_id) if p.engineering_role_id
            else None, "engineering_role": role_name, "is_set_up": True,
            "updated_by": p.updated_by, "updated_at": p.updated_at}


def _words(items: Any, what: str, problems: list[str]) -> list[str]:
    if not isinstance(items, list) or len(items) > 100:
        problems.append(f"{what}: up to 100 entries")
        return []
    seen, out_ = set(), []
    for x in items:
        v = " ".join(str(x or "").split())
        if not v:
            continue
        if len(v) > 100:
            problems.append(f"{what}: each entry is up to 100 characters")
            continue
        if v.lower() not in seen:
            seen.add(v.lower())
            out_.append(v)
    return out_


def clean(data: dict[str, Any], family_ids: set[str]) -> tuple[dict[str, Any], list[str]]:
    """The profile as it will be saved, and what is wrong with it (in words)."""
    problems: list[str] = []
    procs = data.get("processes") or []
    if not isinstance(procs, list) or len(procs) > 40:
        problems.append("Up to 40 processes")
        procs = []
    processes = []
    for p in procs:
        name = " ".join(str((p or {}).get("name") or "").split())
        if not name or len(name) > 100:
            problems.append("Each process needs a name (up to 100 characters)")
            continue
        row: dict[str, Any] = {"name": name}
        for k, label in LIMITS.items():
            v = p.get(k)
            if v in (None, ""):
                row[k] = None
                continue
            try:
                row[k] = float(v)
            except (TypeError, ValueError):
                row[k] = None
                problems.append(f"{name}: {label} must be a number")
                continue
            if row[k] <= 0:
                problems.append(f"{name}: {label} must be more than 0")
        fams = [str(f) for f in p.get("families") or []]
        if any(f not in family_ids for f in fams):
            problems.append(f"{name}: one of its product families no longer exists")
        row["families"] = [f for f in fams if f in family_ids]
        tol = " ".join(str(p.get("tolerance") or "").split())[:60]
        row["tolerance"] = tol or None
        processes.append(row)
    out_ = {"processes": processes}
    names = {"materials": "Materials", "certifications": "Certificates",
             "excluded_industries": "Industries we don't supply",
             "target_industries": "Industries we want"}
    for k in LISTS:
        out_[k] = _words(data.get(k) or [], names[k], problems)
    excluded = {x.lower() for x in out_["excluded_industries"]}
    both = [x for x in out_["target_industries"] if x.lower() in excluded]
    if both:
        problems.append(f"{', '.join(both)} cannot be both wanted and not supplied")
    out_["engineering_role_id"] = data.get("engineering_role_id") or None
    return out_, problems


async def save(session: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any],
               actor: str) -> CapabilityProfile:
    p = await load(session, tenant_id)
    if p is None:
        p = CapabilityProfile(tenant_id=tenant_id, updated_by=actor)
        session.add(p)
    for k, v in data.items():
        setattr(p, k, uuid.UUID(str(v)) if k == "engineering_role_id" and v else v)
    p.updated_by, p.updated_at = actor, datetime.now(UTC)
    await session.flush()
    return p


async def role_name(session: AsyncSession, p: CapabilityProfile | None) -> str | None:
    if p is None or not p.engineering_role_id:
        return None
    r = await session.get(Role, p.engineering_role_id)
    return r.name if r else None
