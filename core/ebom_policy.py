"""Shared validation and compatibility defaults for CAD/EBOM policies."""

CLASSIFICATIONS = ("PHYSICAL", "CAD_ONLY", "REFERENCE", "SKELETON")
EBOM_BEHAVIORS = ("NORMAL", "FLATTEN", "EXCLUDE")
OCCURRENCE_EBOM_BEHAVIORS = ("INHERIT", *EBOM_BEHAVIORS)
REQUIREMENTS = ("REQUIRED", "OPTIONAL", "NOT_REQUIRED")
CAD_CONTROL_MODES = ("CONTROLLED", "SUPPLIER_PACKAGE")

LEGACY_CLASSIFICATION = "PHYSICAL"
LEGACY_DEFAULT_EBOM_BEHAVIOR = "NORMAL"
LEGACY_OCCURRENCE_EBOM_BEHAVIOR = "INHERIT"
LEGACY_REQUIREMENT = "OPTIONAL"
LEGACY_CAD_CONTROL_MODE = "CONTROLLED"


def normalize_choice(value, allowed, default: str, label: str) -> str:
    normalized = str(value or default).strip().upper()
    if normalized not in allowed:
        raise ValueError(f"Invalid {label}: {value!r}.")
    return normalized


def normalize_classification(value) -> str:
    return normalize_choice(
        value, CLASSIFICATIONS, LEGACY_CLASSIFICATION, "classification"
    )


def normalize_default_behavior(value) -> str:
    return normalize_choice(
        value,
        EBOM_BEHAVIORS,
        LEGACY_DEFAULT_EBOM_BEHAVIOR,
        "default EBOM behavior",
    )


def normalize_occurrence_behavior(value) -> str:
    return normalize_choice(
        value,
        OCCURRENCE_EBOM_BEHAVIORS,
        LEGACY_OCCURRENCE_EBOM_BEHAVIOR,
        "occurrence EBOM behavior",
    )


def normalize_requirement(value, label: str) -> str:
    return normalize_choice(value, REQUIREMENTS, LEGACY_REQUIREMENT, label)


def normalize_cad_control_mode(value) -> str:
    return normalize_choice(
        value,
        CAD_CONTROL_MODES,
        LEGACY_CAD_CONTROL_MODE,
        "CAD control mode",
    )


def requires_aes_number(default_ebom_behavior, represented_part_id=None) -> bool:
    """Only objects that can appear as delivery rows require their own AES identity."""
    if represented_part_id not in (None, "", 0, "0"):
        return True
    return normalize_default_behavior(default_ebom_behavior) == "NORMAL"


def recommended_requirements(classification: str) -> tuple[str, str]:
    """Return authoring defaults; migrated data always uses compatibility defaults."""
    classification = normalize_classification(classification)
    if classification == "CAD_ONLY":
        return "REQUIRED", "NOT_REQUIRED"
    if classification in {"REFERENCE", "SKELETON"}:
        return "OPTIONAL", "NOT_REQUIRED"
    return LEGACY_REQUIREMENT, LEGACY_REQUIREMENT


def recommended_default_behavior(classification: str) -> str:
    """Suggest a delivery-safe default for newly authored CAD objects only."""
    classification = normalize_classification(classification)
    if classification == "CAD_ONLY":
        return "FLATTEN"
    if classification in {"REFERENCE", "SKELETON"}:
        return "EXCLUDE"
    return "NORMAL"


def delivery_policy_label(default_behavior: str) -> str:
    behavior = normalize_default_behavior(default_behavior)
    if behavior == "EXCLUDE":
        return "Not for delivery (object and descendants excluded)"
    if behavior == "FLATTEN":
        return "CAD-only grouping (object hidden; children promoted)"
    return "Deliver as a normal EBOM item"
