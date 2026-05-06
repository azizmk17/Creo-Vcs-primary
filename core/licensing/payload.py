"""
core/licensing/payload.py
=========================
Typed, immutable value object representing a fully verified CreoVCS license.

Design notes
------------
* ``frozen=True`` prevents any accidental mutation after the signature has
  been cryptographically verified.  If ``LicensePayload`` were mutable, an
  attacker with control of Python's import machinery could potentially modify
  the payload in memory after verification.  Immutability closes this vector.

* ``datetime`` fields are stored as timezone-aware UTC objects rather than
  raw strings.  This removes all ambiguity from clock comparisons in
  ``checks.py`` and eliminates the need to parse strings at comparison time.

* The ``signature`` field stores the raw base64url string exactly as it
  appears in the ``.lic`` file.  It is kept on the payload for audit logging
  purposes (e.g. writing the signature into an audit record) but is explicitly
  excluded from the canonical serialization used for verification.

* ``machine_id`` defaults to an empty tuple rather than ``None``; an empty
  tuple signals "any machine" while preserving the frozen/hashable contract.
  A ``List`` field cannot be used with ``frozen=True`` + hashing; a tuple is
  used for the internal storage of ``machine_id`` and ``features``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple


@dataclass(frozen=True)
class LicensePayload:
    """Immutable representation of a verified CreoVCS license.

    Instances of this class are produced exclusively by
    ``core.licensing.loader.load_license`` after the Ed25519 signature has
    been successfully verified.  They should never be constructed directly
    by application code.

    Attributes:
        product: Product name string.  Must equal ``"CreoVCS"`` for this
            application; enforced by the loader.
        licensee: Customer name or organisation name as provided by the vendor
            at license issuance time.
        issued_at: UTC-aware ``datetime`` when the license was issued.
            Informational; not used for validity checks.
        expires_at: UTC-aware ``datetime`` after which the license is no
            longer valid.  ``None`` indicates a perpetual (never-expiring)
            license.
        features: Tuple of licensed feature strings, e.g.
            ``("bom_management", "commit", "snapshot")``.  Order is preserved
            from the JSON file but comparisons are by membership, not position.
        signature: Raw base64url Ed25519 signature string, exactly as
            read from the ``.lic`` file.  Stored for audit purposes; excluded
            from canonical serialization.
        version_constraint: Optional PEP-440 / semver constraint string
            describing which application versions this license covers, e.g.
            ``">=4.0,<5.0"``.  ``None`` means any version.  Enforcement is
            the caller's responsibility (not enforced by this module).
        machine_id: Tuple of allowed machine identifier strings produced by
            ``core.licensing.machine.get_machine_id()``.  An empty tuple
            means the license is valid on any machine.

    Example::

        # Constructed by loader.load_license — not directly
        payload: LicensePayload = load_license(path, pub_key)
        print(payload.licensee)        # "Acme Engineering SAS"
        print(payload.expires_at)      # datetime(2027, 2, 18, 0, 0, tzinfo=UTC)
        print("commit" in payload.features)  # True
    """

    product: str
    licensee: str
    issued_at: datetime
    expires_at: Optional[datetime]
    features: Tuple[str, ...]
    signature: str
    version_constraint: Optional[str] = None
    machine_id: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate internal consistency of the payload after construction.

        Raises:
            TypeError: If ``issued_at`` or ``expires_at`` are not
                timezone-aware datetimes.
            ValueError: If ``expires_at`` is earlier than ``issued_at``.

        Note:
            These checks are a defence-in-depth measure; the loader already
            performs field validation before constructing the payload.
        """
        if self.issued_at.tzinfo is None:
            raise TypeError("LicensePayload.issued_at must be a timezone-aware datetime.")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None:
                raise TypeError("LicensePayload.expires_at must be a timezone-aware datetime.")
            if self.expires_at <= self.issued_at:
                raise ValueError(
                    f"expires_at ({self.expires_at!r}) must be after "
                    f"issued_at ({self.issued_at!r})."
                )
