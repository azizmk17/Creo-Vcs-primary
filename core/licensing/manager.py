"""
core/licensing/manager.py
=========================
Thread-safe singleton ``LicenseManager`` and the ``@require_license_feature``
decorator for the CreoVCS license verification system.

This module is the primary integration point between the licensing subsystem
and the rest of the application.  Application code should interact with the
licensing system exclusively through ``LicenseManager`` and
``require_license_feature`` rather than calling the lower-level functions in
``checks.py``, ``crypto.py``, or ``loader.py`` directly.

Singleton design
----------------
``LicenseManager`` uses a class-level singleton (identical in spirit to the
existing ``SessionManager``) but adds proper thread-safety via a
``threading.Lock``.  The lock is only held during the ``initialize()`` call;
all subsequent reads (``require_feature``, ``is_licensed_for``, ``payload``)
are lock-free because:

1. ``_payload`` is set atomically once (Python's GIL guarantees reference
   assignment is atomic).
2. ``_payload`` is an immutable frozen dataclass after construction — readers
   never see a partially-constructed object.

Integration pattern
-------------------
In ``main3.py`` (after ``app.setStyle("Fusion")`` and before ``LoginPage``):

.. code-block:: python

    from pathlib import Path
    from core.licensing import LicenseManager, LicenseError
    import os

    pub_key = bytes.fromhex(os.environ["CREOVCS_PUBLIC_KEY_HEX"])
    try:
        LicenseManager.initialize(Path("creovcs.lic"), pub_key)
    except LicenseError as exc:
        QMessageBox.critical(None, "License Error", str(exc))
        sys.exit(1)

In service methods (mirrors the existing ``@require_permission`` pattern):

.. code-block:: python

    from core.licensing.manager import require_license_feature

    class CommitService:
        @require_license_feature("commit")
        def submit_commit(self, part_id: int, ...) -> None:
            ...

In UI code (for enabling/disabling buttons):

.. code-block:: python

    from core.licensing.manager import LicenseManager

    adv_diff_btn.setEnabled(LicenseManager.is_licensed_for("advanced_diff"))
"""

from __future__ import annotations

import threading
from functools import wraps
from pathlib import Path
from typing import Optional

from core.licensing.checks import check_expiry, check_feature, check_machine
from core.licensing.exceptions import LicenseError
from core.licensing.loader import load_license
from core.licensing.payload import LicensePayload
from core.licensing.revocation import check_file_revocation


class LicenseManager:
    """Thread-safe singleton that holds the verified license for this process.

    The manager must be initialised exactly once (via ``initialize()``) before
    any ``require_feature()`` or ``is_licensed_for()`` call.  After
    initialisation the internal state is immutable — the lock is only held
    during the first initialisation.

    Class attributes:
        _instance: The singleton instance (created lazily).
        _lock: Class-level lock protecting the initialisation path.
        _payload: The verified ``LicensePayload`` after initialisation.
        _initialized: ``True`` after the first successful ``initialize()`` call.

    Security model:
        The public key is accepted as a parameter and never stored inside
        this class.  It is used once during ``initialize()`` and then
        discarded.  The verified ``LicensePayload`` is stored.

        Feature checks at runtime are O(n) membership tests on the small
        ``payload.features`` tuple.  For a typical license with fewer than
        20 features this is negligible.
    """

    _instance: Optional["LicenseManager"] = None
    _lock: threading.Lock = threading.Lock()
    _payload: Optional[LicensePayload] = None
    _initialized: bool = False

    # Path to the shared-folder revoked.json file.
    # Admin edits this file to revoke a license — no rebuild needed.
    # Set to None to disable the revocation check.
    _revocation_path: Optional[Path] = None

    def __new__(cls) -> "LicenseManager":
        # Standard double-checked locking pattern for lazy singleton creation.
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def initialize(
        cls,
        path: Path,
        public_key_bytes: bytes,
        revocation_path: Optional[Path] = None,
    ) -> "LicenseManager":
        """Load, verify, and cache the license for this process.

        This method is idempotent: if called a second time after a successful
        first call, it returns the existing singleton immediately without
        re-reading the file or re-verifying the signature.

        The method must be called before any feature checks.  It is safe to
        call from any thread; only the first successful call has any effect.

        Verification order:

        1. ``load_license`` — file I/O, JSON parsing, Ed25519 signature
           verification.
        2. ``check_expiry``  — current UTC time vs ``expires_at``.
        3. ``check_machine`` — current machine ID vs ``machine_id`` list.

        Feature checking is NOT performed here; it is the caller's
        responsibility to call ``require_feature`` or ``is_licensed_for``
        for any features gated at runtime.

        Args:
            path: Path to the signed ``.lic`` file.
            public_key_bytes: Raw 32-byte Ed25519 public key.  Must not be
                hard-coded; obtain from an environment variable or build
                constant generated by CI/CD.

        Returns:
            The singleton ``LicenseManager`` instance.

        Raises:
            LicenseFileNotFoundError: License file missing or unreadable.
            LicenseParseError: File is malformed or missing required fields.
            LicenseSignatureError: Signature invalid (tampered or wrong key).
            LicenseExpiredError: License has expired.
            LicenseMachineError: Current machine not in the allowed list.

        Example::

            from pathlib import Path
            from core.licensing.manager import LicenseManager
            import os

            pub_key = bytes.fromhex(os.environ["CREOVCS_PUBLIC_KEY_HEX"])
            LicenseManager.initialize(Path("creovcs.lic"), pub_key)
        """
        # Fast-path: already initialized — no lock needed.
        if cls._initialized:
            return cls._instance or cls()

        with cls._lock:
            # Re-check inside the lock in case another thread initialized
            # between the fast-path check and acquiring the lock.
            if cls._initialized:
                return cls._instance or cls()

            # Load and verify (raises on any failure).
            payload = load_license(path, public_key_bytes)
            check_expiry(payload)
            check_machine(payload)

            # Shared-folder revocation check — runs only if a path was supplied.
            # Fail-open: a missing or unreadable file is silently ignored.
            # Only an explicit entry in revoked.json blocks the license.
            cls._revocation_path = revocation_path
            if revocation_path:
                check_file_revocation(payload.signature, revocation_path, public_key_bytes)

            # Store atomically — Python reference assignment is GIL-atomic.
            cls._payload = payload
            cls._initialized = True

        return cls._instance or cls()

    @classmethod
    def require_feature(cls, feature: str) -> None:
        """Assert that *feature* is licensed; raise if not.

        This is the raising variant intended for use in service methods and
        the ``@require_license_feature`` decorator.  Call it wherever a
        feature must be licensed for an operation to proceed.

        Args:
            feature: The feature string to assert, e.g. ``"commit"``.
                Must match exactly (case-sensitive) what appears in the
                ``.lic`` file.

        Raises:
            LicenseError: If ``initialize()`` has not been called yet.
                This is a programming error; call ``initialize()`` at
                application startup before any feature checks.
            LicenseFeatureError: If *feature* is not in the license's
                ``features`` list.

        Example::

            LicenseManager.require_feature("bom_management")
            # Raises LicenseFeatureError if not licensed.
        """
        if not cls._initialized or cls._payload is None:
            raise LicenseError(
                "LicenseManager has not been initialised. "
                "Call LicenseManager.initialize() at application startup."
            )
        check_feature(cls._payload, feature)

    @classmethod
    def is_licensed_for(cls, feature: str) -> bool:
        """Return ``True`` if *feature* is present in the license.

        This is the non-raising variant intended for UI enable/disable logic
        (e.g. ``button.setEnabled(LicenseManager.is_licensed_for("x"))``).

        If ``initialize()`` has not been called, returns ``False`` rather
        than raising.  This is a deliberate design choice: UI code that runs
        before the license is loaded (which should not happen in normal
        operation) will default to disabled rather than crashing.

        Args:
            feature: The feature string to check.

        Returns:
            ``True`` if the active license includes *feature*; ``False``
            if the license is not loaded or the feature is absent.

        Example::

            adv_diff_btn.setEnabled(LicenseManager.is_licensed_for("advanced_diff"))
        """
        if not cls._initialized or cls._payload is None:
            return False
        return feature in cls._payload.features

    @classmethod
    def get_payload(cls) -> LicensePayload:
        """Return the verified license payload.

        Use this to access license metadata (licensee name, expiry date,
        feature list) for display in About dialogs or audit logging.

        Returns:
            The verified, immutable ``LicensePayload``.

        Raises:
            LicenseError: If ``initialize()`` has not been called yet.

        Example::

            payload = LicenseManager.get_payload()
            label.setText(f"Licensed to: {payload.licensee}")
        """
        if not cls._initialized or cls._payload is None:
            raise LicenseError(
                "LicenseManager has not been initialised. "
                "Call LicenseManager.initialize() at application startup."
            )
        return cls._payload

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton to an uninitialised state.

        **For use in tests only.**  Production code must never call this
        method.  It allows test suites to re-initialise the manager with
        different licenses between test cases.

        Example (pytest)::

            def test_expired_license(valid_lic_file, key_pair):
                LicenseManager.reset()
                with pytest.raises(LicenseExpiredError):
                    LicenseManager.initialize(expired_lic_path, key_pair[1])
        """
        with cls._lock:
            cls._payload = None
            cls._initialized = False


# ---------------------------------------------------------------------------
# @require_license_feature decorator
# ---------------------------------------------------------------------------

def require_license_feature(feature: str):
    """Decorator that enforces a license feature on the decorated callable.

    Designed to mirror the existing ``require_permission`` decorator in
    ``core.services.permission_decorators`` so that both permission and
    license enforcement follow the same idiomatic pattern.

    The decorated function will raise ``LicenseFeatureError`` if the
    feature is not present in the loaded license.  Any other ``LicenseError``
    (e.g. licence not loaded) will also propagate.

    Args:
        feature: The license feature string required by the decorated
            callable, e.g. ``"advanced_diff"``.  Must be one of the
            ``FEATURE_*`` constants defined in ``core.licensing``.

    Returns:
        A decorator that wraps the target function/method.

    Raises:
        LicenseFeatureError: At call time if *feature* is not licensed.
        LicenseError: At call time if the manager has not been initialised.

    Example (service method)::

        from core.licensing.manager import require_license_feature

        class CommitService:
            @require_license_feature("commit")
            def submit_commit(self, part_id: int) -> None:
                ...

    Example (standalone function)::

        @require_license_feature("advanced_diff")
        def run_step_diff(part_id: int) -> dict:
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            LicenseManager.require_feature(feature)
            return func(*args, **kwargs)
        return wrapper
    return decorator
