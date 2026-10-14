"""Sample options models (decision C4 A: one sample configurable family, flanges, for the
demo and the tests; decision C18 A: Claude's sample data). A company edits or replaces
them on the family's Options and rules screen."""

from typing import Any

_SIZES = [("15", '1/2" (15 NB)'), ("20", '3/4" (20 NB)'), ("25", '1" (25 NB)'),
          ("40", '1 1/2" (40 NB)'), ("50", '2" (50 NB)'), ("80", '3" (80 NB)'),
          ("100", '4" (100 NB)'), ("150", '6" (150 NB)'), ("200", '8" (200 NB)'),
          ("250", '10" (250 NB)'), ("300", '12" (300 NB)'), ("350", '14" (350 NB)'),
          ("400", '16" (400 NB)'), ("450", '18" (450 NB)'), ("500", '20" (500 NB)'),
          ("600", '24" (600 NB)')]

FLANGES: dict[str, Any] = {
    "code_pattern": "FL-{size}-{class}-{material}-{facing}",
    "attributes": [
        {"key": "size", "label": "Size", "kind": "choice",
         "options": [{"value": v, "label": label, "code": v} for v, label in _SIZES]},
        {"key": "class", "label": "Pressure class", "kind": "choice",
         "options": [{"value": c, "label": f"Class {c}", "code": c}
                     for c in ("150", "300", "600", "900", "1500", "2500")]},
        {"key": "material", "label": "Material", "kind": "choice", "options": [
            {"value": "A105", "label": "Carbon steel ASTM A105", "code": "A105"},
            {"value": "F304", "label": "Stainless ASTM A182 F304", "code": "F304"},
            {"value": "F316", "label": "Stainless ASTM A182 F316", "code": "F316"},
            {"value": "LF2", "label": "Low-temperature ASTM A350 LF2", "code": "LF2"}]},
        {"key": "facing", "label": "Facing", "kind": "choice", "options": [
            {"value": "RF", "label": "Raised face", "code": "RF"},
            {"value": "FF", "label": "Flat face", "code": "FF"},
            {"value": "RTJ", "label": "Ring-type joint", "code": "RTJ"}]},
    ],
    "rules": [
        {"id": "R-1", "kind": "only_when",
         "then": {"attr": "facing", "in": ["FF"]},
         "when": [{"attr": "class", "in": ["150"]}],
         "message": "Flat face is made only for Class 150"},
        {"id": "R-2", "kind": "requires",
         "when": [{"attr": "class", "in": ["900", "1500", "2500"]}],
         "then": {"attr": "facing", "in": ["RTJ"]},
         "message": "Class 900 and above need a ring-type joint face"},
        {"id": "R-3", "kind": "excludes",
         "when": [{"attr": "class", "in": ["2500"]}],
         "then": {"attr": "size", "in": ["350", "400", "450", "500", "600"]},
         "message": "Class 2500 flanges are made only up to 12 inch"},
        {"id": "R-4", "kind": "excludes",
         "when": [{"attr": "material", "in": ["LF2"]}],
         "then": {"attr": "size", "in": ["450", "500", "600"]},
         "message": "LF2 forgings are bought in only up to 16 inch"},
    ],
}

SAMPLES: dict[str, dict[str, Any]] = {"flanges": FLANGES}


# The flanges sample cost model (F3.2, C18 A: Claude's sample data). Weights are
# approximate weld-neck weights in kg and the bolt holes follow ASME B16.5; a company
# pastes its own tables over them from Excel.
_CLASSES = ("150", "300", "600", "900", "1500", "2500")
_WEIGHTS = {  # size: kg per class, in the order of _CLASSES (Class 2500 only to 12")
    "15": (0.9, 0.9, 0.9, 3.2, 3.2, 4.1), "20": (0.9, 1.4, 1.4, 3.6, 3.6, 4.1),
    "25": (1.1, 1.8, 1.8, 4.1, 4.1, 5.9), "40": (1.6, 3.2, 3.6, 6.4, 6.4, 10.5),
    "50": (2.7, 3.6, 4.5, 11.4, 11.4, 14.1), "80": (4.6, 6.8, 7.7, 13.6, 20.5, 29.5),
    "100": (6.8, 11.4, 17.3, 20.5, 31.8, 44.5), "150": (10.9, 19.1, 36.4, 43.2, 72.7, 118),
    "200": (17.7, 28.6, 53.6, 72.7, 118, 177), "250": (24.5, 40.9, 85.9, 104, 191, 313),
    "300": (36.8, 56.8, 103, 145, 268, 463), "350": (50.9, 79.5, 154, 177, 349, None),
    "400": (63.6, 102, 210, 209, 456, None), "450": (68.1, 129, 268, 315, 614, None),
    "500": (86.3, 163, 330, 390, 775, None), "600": (124, 243, 513, 658, 1251, None),
}
_HOLES = {
    "15": (4, 4, 4, 4, 4, 4), "20": (4, 4, 4, 4, 4, 4), "25": (4, 4, 4, 4, 4, 4),
    "40": (4, 4, 4, 4, 4, 4), "50": (4, 8, 8, 8, 8, 8), "80": (4, 8, 8, 8, 8, 8),
    "100": (8, 8, 8, 8, 8, 8), "150": (8, 12, 12, 12, 12, 8), "200": (8, 12, 12, 12, 12, 12),
    "250": (12, 16, 16, 16, 12, 12), "300": (12, 16, 20, 20, 16, 12),
    "350": (12, 20, 20, 20, 16, None), "400": (16, 20, 20, 20, 16, None),
    "450": (16, 24, 20, 20, 16, None), "500": (20, 24, 24, 20, 16, None),
    "600": (20, 24, 24, 20, 16, None),
}


def _table(keys: list[str], data: dict[str, tuple[float | int | None, ...]]) -> dict[str, Any]:
    return {"keys": keys, "rows": [{"key": [size, c], "value": str(v)}
                                   for size, vals in data.items()
                                   for c, v in zip(_CLASSES, vals, strict=True) if v is not None]}


FLANGES_COST: dict[str, Any] = {
    "tables": {"weight": _table(["size", "class"], _WEIGHTS),
               "holes": _table(["size", "class"], _HOLES)},
    "values": [{"name": "finished_kg", "label": "Finished weight (kg)",
                "formula": "lookup(weight, size, class)"}],
    "materials": [{"label": "Forging", "code": "material", "qty": "finished_kg",
                   "unit": "kg"}],
    "operations": [
        {"label": "Turning and facing", "machine": "CNC Lathe",
         "minutes": "max(10, 6 + finished_kg * 0.8)"},
        {"label": "Drilling", "machine": "VMC",
         "minutes": "lookup(holes, size, class) * if(class >= 900, 2.5, 1.5)"},
        {"label": "Ring groove", "machine": "CNC Lathe",
         "minutes": 'if(facing == "RTJ", 12, 0)'},
    ],
    "tooling": [{"label": "RTJ ring gauge", "amount": 'if(facing == "RTJ", 6500, 0)'}],
    "tooling_spread": False,
    "overhead_pct": "12",
    "packing": {"basis": "pct", "value": "2"},
    "packed_kg": "finished_kg * 1.03",
    "packed_m3": "finished_kg * 0.0004",
    "rate_card": {"basis": "kg", "weight": "finished_kg",
                  "rate": 'if(material == "A105", 165, if(material == "LF2", 195, '
                          'if(material == "F304", 470, 560)))'},
}

# Sample rates the flanges model needs, added only where the company has none.
SAMPLE_MATERIALS = [
    {"code": "A105", "name": "Carbon steel forging ASTM A105", "unit": "kg", "rate": "95",
     "yield_pct": "85", "scrap_rate": "28"},
    {"code": "F304", "name": "Stainless forging ASTM A182 F304", "unit": "kg", "rate": "330",
     "yield_pct": "85", "scrap_rate": "110"},
    {"code": "F316", "name": "Stainless forging ASTM A182 F316", "unit": "kg", "rate": "410",
     "yield_pct": "85", "scrap_rate": "140"},
    {"code": "LF2", "name": "Low-temperature forging ASTM A350 LF2", "unit": "kg",
     "rate": "120", "yield_pct": "85", "scrap_rate": "30"},
]
SAMPLE_MACHINES = [
    {"code": "CNC Lathe", "name": "CNC lathe", "hour_rate": "650"},
    {"code": "VMC", "name": "Vertical machining centre", "hour_rate": "900"},
]
SAMPLE_NOTE = "Sample rate (C18 A): replace it with your own"
COST_SAMPLES: dict[str, dict[str, Any]] = {"flanges": FLANGES_COST}
