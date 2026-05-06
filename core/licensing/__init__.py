"""
core/licensing — CreoVCS license verification subsystem.
=========================================================

Security model
--------------
License files (``.lic``) are signed JSON documents.  The vendor (you)
generates an Ed25519 key pair using the offline tool
``tools/licensing/keygen.py``.  The private key is kept secure (never
committed to source control).  The public key is embedded in the built
application binary by the CI/CD pipeline.

Each license file is created by the offline tool
``tools/licensing/sign_license.py``, which:

1. Reads a JSON template containing the license fields.
2. Produces the *canonical form*: JSON with sorted keys, no optional
   whitespace, non-ASCII characters escaped, UTF-8 encoded.
3. Signs the canonical bytes with the Ed25519 private key.
4. Appends the base64url-encoded signature as the ``"signature"`` field.
5. Writes the complete JSON object to the ``.lic`` file.

At runtime, ``load_license`` performs the exact reverse:

1. Reads and JSON-parses the ``.lic`` file.
2. Reconstructs the canonical bytes (minus the ``"signature"`` key).
3. Verifies the Ed25519 signature against those bytes using the public key.
4. Returns an immutable ``LicensePayload`` if verification succeeds.

No field can be changed in the license file without invalidating the
signature.  The public key is the only secret distributed with the
application; the private key never leaves the vendor's secure environment.

Module structure
----------------
``exceptions``  — Exception hierarchy (import for ``except`` clauses).
``payload``     — ``LicensePayload`` frozen dataclass.
``crypto``      — ``canonical_serialize``, ``verify_signature`` (Cython-safe).
``machine``     — ``get_machine_id()`` cross-platform (Cython-safe).
``checks``      — ``check_expiry``, ``check_feature``, ``check_machine``.
``loader``      — ``load_license()`` orchestrator.
``manager``     — ``LicenseManager`` singleton + ``require_license_feature``.

Quick-start example
-------------------
**Step 1 — Generate a key pair (offline, run once):**

.. code-block:: bash

    python tools/licensing/keygen.py --out-dir ./keys
    # Creates: keys/ed25519_private.key  (keep secret)
    #           keys/ed25519_public.key   (embed in build)

**Step 2 — Create and sign a license (offline, per customer):**

.. code-block:: bash

    python tools/licensing/sign_license.py \\
        --template license_template.json \\
        --private-key keys/ed25519_private.key \\
        --out acme_engineering.lic

License template (``license_template.json``)::

    {
        "product":    "CreoVCS",
        "licensee":   "Acme Engineering SAS",
        "issued_at":  "2026-02-18T00:00:00+00:00",
        "expires_at": "2027-02-18T00:00:00+00:00",
        "features":   ["bom_management", "commit", "snapshot"],
        "version_constraint": ">=4.0,<5.0",
        "machine_id": []
    }

Set ``"machine_id"`` to a non-empty list (e.g. ``["a1b2c3d4..."]``) for a
node-locked license.  Get the machine ID by running::

    python -c "from core.licensing.machine import get_machine_id; print(get_machine_id())"

**Step 3 — Runtime initialisation (in ``main3.py``):**

.. code-block:: python

    import os
    from pathlib import Path
    from core.licensing import LicenseManager, LicenseError

    pub_key = bytes.fromhex(os.environ["CREOVCS_PUBLIC_KEY_HEX"])
    try:
        LicenseManager.initialize(Path("creovcs.lic"), pub_key)
    except LicenseError as exc:
        QMessageBox.critical(None, "License Error", str(exc))
        sys.exit(1)

**Step 4 — Feature gating (decorator style):**

.. code-block:: python

    from core.licensing import require_license_feature

    class CommitService:
        @require_license_feature("commit")
        def submit_commit(self, part_id: int) -> None:
            ...

**Step 5 — Feature gating (UI enable/disable style):**

.. code-block:: python

    from core.licensing import LicenseManager

    adv_diff_btn.setEnabled(LicenseManager.is_licensed_for("advanced_diff"))

**Step 6 — Display license info in an About dialog:**

.. code-block:: python

    from core.licensing import LicenseManager

    payload = LicenseManager.get_payload()
    label.setText(
        f"Licensed to: {payload.licensee}\\n"
        f"Expires: {payload.expires_at.strftime('%Y-%m-%d') if payload.expires_at else 'Never'}"
    )
"""

from core.licensing.exceptions import (
    LicenseError,
    LicenseExpiredError,
    LicenseFeatureError,
    LicenseFileNotFoundError,
    LicenseMachineError,
    LicenseParseError,
    LicenseSignatureError,
)
from core.licensing.manager import LicenseManager, require_license_feature
from core.licensing.payload import LicensePayload

# ---------------------------------------------------------------------------
# Canonical feature name constants
#
# Use these constants in @require_license_feature decorators and
# LicenseManager.is_licensed_for() calls throughout the codebase.  Using
# constants prevents typos and makes it easy to search for all usages of a
# given feature gate.
#
# The values must match exactly what is written into the .lic file by
# tools/licensing/sign_license.py.
# ---------------------------------------------------------------------------

#: Core BOM management — BomPage, BomService.
FEATURE_BOM_MANAGEMENT: str = "bom_management"

#: Commit/validate/merge workflow — CommitPage, CommitService.
FEATURE_COMMIT: str = "commit"

#: Snapshot capture and restore — SnapshotPage, SnapshotService.
FEATURE_SNAPSHOT: str = "snapshot"

#: STEP-file diff analysis — tools/CAD/step_diff_engine.
FEATURE_ADVANCED_DIFF: str = "advanced_diff"

#: Administrative panel — AdminPage, AdminService.
FEATURE_ADMIN: str = "admin"

#: Baseline management — BaselineService.
FEATURE_BASELINE: str = "baseline"

#: Package export (PDF/STEP delivery with manifest) — PackageExportService.
FEATURE_PACKAGE_EXPORT: str = "package_export"

__all__ = [
    # Exceptions
    "LicenseError",
    "LicenseExpiredError",
    "LicenseFeatureError",
    "LicenseFileNotFoundError",
    "LicenseMachineError",
    "LicenseParseError",
    "LicenseSignatureError",
    # Core types
    "LicensePayload",
    # Manager and decorator
    "LicenseManager",
    "require_license_feature",
    # Feature constants
    "FEATURE_BOM_MANAGEMENT",
    "FEATURE_COMMIT",
    "FEATURE_SNAPSHOT",
    "FEATURE_ADVANCED_DIFF",
    "FEATURE_ADMIN",
    "FEATURE_BASELINE",
    "FEATURE_PACKAGE_EXPORT",
]
