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
