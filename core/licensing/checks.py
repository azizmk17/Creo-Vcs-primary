"""
core/licensing/checks.py
========================
Runtime validation checks for an already-signature-verified ``LicensePayload``.

These functions are deliberately separate from ``crypto.py`` (signature
verification) and ``loader.py`` (file loading and parsing).  The separation
allows each check to be called independently — for example, ``check_expiry``
can be called periodically during a long session without re-reading the file.

All functions follow the same contract:

* Accept a ``LicensePayload`` as the first argument.
* Return ``None`` on success (no return value needed — success is silence).
* Raise a specific ``LicenseError`` subclass on failure.
* Never raise any other exception type.

Obfuscation readiness
---------------------
All functions are pure (no global state, no I/O beyond reading the machine ID).
No ``eval``, ``exec``, or ``inspect`` usage.  Suitable for Cython/Nuitka
compilation without modification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.licensing.payload import LicensePayload


def check_expiry(payload: "LicensePayload") -> None:
    """Raise ``LicenseExpiredError`` if the license has passed its expiry date.

    A perpetual license (``payload.expires_at is None``) always passes this
    check regardless of the current time.

    The comparison is performed against ``datetime.now(timezone.utc)``, which
    uses the local system clock.  For a desktop application this is the
    appropriate source of truth; server-side applications may wish to add NTP
    verification on top of this check.

    Args:
        payload: A signature-verified ``LicensePayload``.  Must have a
            timezone-aware ``expires_at`` if not ``None`` (enforced by
            ``LicensePayload.__post_init__``).

    Raises:
        LicenseExpiredError: If ``payload.expires_at`` is not ``None`` and
            the current UTC time is equal to or past ``payload.expires_at``.

    Example::

        check_expiry(payload)      # raises if expired; returns None otherwise
    """
    from core.licensing.exceptions import LicenseExpiredError

    if payload.expires_at is None:
        # Perpetual license — always valid.
        return

    now_utc = datetime.now(timezone.utc)
    if now_utc >= payload.expires_at:
        expiry_str = payload.expires_at.strftime("%Y-%m-%d")
        raise LicenseExpiredError(
            f"Your CreoVCS license expired on {expiry_str}. "
            "Please contact your vendor to renew."
        )


def check_feature(payload: "LicensePayload", feature: str) -> None:
    """Raise ``LicenseFeatureError`` if *feature* is not licensed.

    Comparison is exact and case-sensitive.  Feature strings are defined by
    the vendor and must match exactly what appears in the signed license file.
    See the ``FEATURE_*`` constants in ``core.licensing`` for the canonical
    names used by CreoVCS.

    Args:
        payload: A signature-verified ``LicensePayload``.
        feature: The feature string to check, e.g. ``"bom_management"``.

    Raises:
        LicenseFeatureError: If *feature* is not present in
            ``payload.features``.

    Example::

        check_feature(payload, "advanced_diff")
        # Raises LicenseFeatureError if not in payload.features
    """
    from core.licensing.exceptions import LicenseFeatureError

    if feature not in payload.features:
        raise LicenseFeatureError(
            f"Feature '{feature}' is not included in your CreoVCS license. "
            "Contact your vendor to upgrade your license."
        )


def check_machine(payload: "LicensePayload") -> None:
    """Raise ``LicenseMachineError`` if this machine is not in the allowed list.

    If ``payload.machine_id`` is ``None`` or an empty tuple/sequence, the
    check is skipped entirely — the license is valid on any machine.  This
    allows the vendor to issue both node-locked and floating licenses using
    the same license format.

    The current machine's ID is obtained via
    ``core.licensing.machine.get_machine_id()``, which never raises.

    Args:
        payload: A signature-verified ``LicensePayload``.

    Raises:
        LicenseMachineError: If ``payload.machine_id`` is non-empty and the
            current machine's ID is not in it.

    Example::

        check_machine(payload)
        # Raises LicenseMachineError if machine_id list is present and
        # the current machine is not in it.

    Note:
        The current machine ID is included in the error message so that
        administrators can easily identify which ID to add to the license.
    """
    from core.licensing.exceptions import LicenseMachineError
    from core.licensing.machine import get_machine_id

    if not payload.machine_id:
        # Empty list or None — floating license, valid on any machine.
        return

    current_id = get_machine_id()
    if current_id not in payload.machine_id:
        raise LicenseMachineError(
            f"This machine (ID: {current_id}) is not authorised by your CreoVCS license. "
            "Contact your administrator to add this machine to the license."
        )
