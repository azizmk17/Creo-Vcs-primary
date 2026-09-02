"""Windchill-style Item master attributes and normalization rules."""

ITEM_TYPES = (
    "MECHANICAL_PART",
    "SOFTWARE_PART",
    "PURCHASED_PART",
    "REFERENCE_PART",
)
ASSEMBLY_MODES = ("COMPONENT", "SEPARABLE", "INSEPARABLE")
PROCUREMENT_SOURCES = ("MAKE", "BUY", "MAKE_OR_BUY")
ITEM_VIEWS = ("DESIGN", "MANUFACTURING", "SERVICE")
DEFAULT_UNITS = ("EA", "KG", "M", "MM", "L", "SET")

ITEM_NUMBER_START = 50_000_000
ITEM_NUMBER_WIDTH = 8


def _normalize(value, allowed, default: str, label: str) -> str:
    normalized = str(value or default).strip().upper().replace(" ", "_")
    if normalized not in allowed:
        raise ValueError(f"Invalid {label}: {value!r}.")
    return normalized


def normalize_item_type(value) -> str:
    return _normalize(value, ITEM_TYPES, "MECHANICAL_PART", "Item type")


def normalize_assembly_mode(value) -> str:
    return _normalize(value, ASSEMBLY_MODES, "COMPONENT", "assembly mode")


def normalize_procurement_source(value) -> str:
    return _normalize(value, PROCUREMENT_SOURCES, "MAKE", "source")


def normalize_item_view(value) -> str:
    return _normalize(value, ITEM_VIEWS, "DESIGN", "Item view")


def normalize_default_unit(value) -> str:
    return _normalize(value, DEFAULT_UNITS, "EA", "default unit")


def item_type_defaults(item_type: str) -> dict:
    """Return authoring defaults without changing persisted legacy Items."""
    item_type = normalize_item_type(item_type)
    if item_type == "SOFTWARE_PART":
        return {
            "procurement_source": "MAKE",
            "cad_requirement": "NOT_REQUIRED",
            "drawing_requirement": "NOT_REQUIRED",
            "deliverable": True,
        }
    if item_type == "PURCHASED_PART":
        return {
            "procurement_source": "BUY",
            "cad_requirement": "NOT_REQUIRED",
            "drawing_requirement": "NOT_REQUIRED",
            "deliverable": True,
        }
    if item_type == "REFERENCE_PART":
        return {
            "procurement_source": "MAKE_OR_BUY",
            "cad_requirement": "NOT_REQUIRED",
            "drawing_requirement": "NOT_REQUIRED",
            "deliverable": False,
        }
    return {
        "procurement_source": "MAKE",
        "cad_requirement": "OPTIONAL",
        "drawing_requirement": "OPTIONAL",
        "deliverable": True,
    }
