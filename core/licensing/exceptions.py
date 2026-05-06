"""
core/licensing/exceptions.py
============================
Exception hierarchy for the CreoVCS license verification system.

All public exceptions inherit from ``LicenseError`` so callers can catch
the entire family with a single ``except LicenseError`` clause while still
being able to distinguish individual failure modes when needed.

Security note
-------------
When surfacing license errors to end users, display only the exception
*message* — never include inner exception details (``__cause__``) in a
user-visible dialog.  The inner cause may contain cryptographic
implementation details that should not be disclosed.

Usage::

    from core.licensing.exceptions import LicenseError, LicenseExpiredError

    try:
        LicenseManager.initialize(path, pub_key)
    except LicenseExpiredError as exc:
        show_error(str(exc))
    except LicenseError as exc:
        show_error(str(exc))
"""


class LicenseError(RuntimeError):
    """Base class for all license verification failures.

    All license-related exceptions inherit from this class so application
    code can use a single ``except LicenseError`` guard where fine-grained
    handling is not required.

    Args:
        message: Human-readable description of the failure. Should be
            safe to display to end users.
    """


class LicenseFileNotFoundError(LicenseError):
    """Raised when the license file path does not exist or cannot be opened.

    Args:
        path: The filesystem path that could not be opened.

    Example::

        raise LicenseFileNotFoundError(
            f"License file not found: {path}. "
            "Contact your administrator to obtain a valid license."
        )
    """


class LicenseParseError(LicenseError):
    """Raised when the license file is not valid JSON or is missing required fields.

    This exception covers both malformed JSON and structurally valid JSON
    that is missing fields required by the license schema (e.g. ``product``,
    ``signature``).

    Args:
        message: Details of the parse failure, safe to show to the user.

    Example::

        raise LicenseParseError(
            "License file is missing required field 'product'."
        )
    """


class LicenseSignatureError(LicenseError):
    """Raised when the Ed25519 signature does not match the canonical payload.

    This is the primary tamper-detection exception.  It is raised when:

    * The ``"signature"`` field cannot be base64url-decoded.
    * The decoded signature bytes fail Ed25519 verification.
    * The public key bytes are malformed.

    Security note: Do NOT expose the raw cryptographic error text to end
    users in production; the generic message "License signature is invalid"
    is sufficient and avoids leaking implementation details.

    Example::

        raise LicenseSignatureError(
            "License signature is invalid — the file may have been tampered with."
        )
    """


class LicenseExpiredError(LicenseError):
    """Raised when the current UTC time is past the license ``expires_at`` field.

    A perpetual license (``expires_at`` is ``None``) never triggers this
    exception.

    Args:
        expires_at: The expiry timestamp string from the license payload,
            included in the message so users know when the license lapsed.

    Example::

        raise LicenseExpiredError(
            f"License expired on {expires_at.strftime('%Y-%m-%d')}. "
            "Please renew your license."
        )
    """


class LicenseFeatureError(LicenseError):
    """Raised when a requested feature is not present in the license.

    This exception is raised by ``LicenseManager.require_feature()`` and
    the ``@require_license_feature`` decorator when the active license does
    not include the requested feature string.

    Args:
        feature: The feature name that was requested.
        available: The list of features that ARE licensed, for diagnostic
            purposes.  Do not surface this list to untrusted users.

    Example::

        raise LicenseFeatureError(
            "Feature 'advanced_diff' is not included in your license. "
            "Contact your vendor to upgrade."
        )
    """


class LicenseMachineError(LicenseError):
    """Raised when the current machine is not in the license's allowed machine list.

    This exception is only raised when the license includes a non-empty
    ``machine_id`` list and the current machine's ID is absent from it.
    Licenses with an empty or absent ``machine_id`` list are valid on any
    machine.

    Args:
        current_id: The machine ID computed at runtime.  Include this in
            the error message so administrators can add the machine to the
            license if needed.

    Example::

        raise LicenseMachineError(
            f"This machine (ID: {current_id}) is not authorised by the license. "
            "Contact your administrator to add this machine."
        )
    """
