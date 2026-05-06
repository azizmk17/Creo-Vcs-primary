"""
tools/licensing/sign_revocation.py
====================================
Offline tool for creating and updating a signed ``revoked.json`` file.

Run this on a machine that has access to the Ed25519 private key (produced
by ``keygen.py``).  The output file is placed directly in the shared folder
— no application rebuild or restart is needed.

Usage examples
--------------
**Start a new revocation list with one entry:**

.. code-block:: bash

    python tools/licensing/sign_revocation.py \\
        --add "base64urlSignatureFromCustomerLicFile" \\
        --private-key keys/ed25519_private.key \\
        --out "\\\\\\\\server\\\\share\\\\revoked.json"

**Add another entry to an existing list:**

.. code-block:: bash

    python tools/licensing/sign_revocation.py \\
        --add "anotherBase64urlSignature" \\
        --existing "\\\\\\\\server\\\\share\\\\revoked.json" \\
        --private-key keys/ed25519_private.key \\
        --out "\\\\\\\\server\\\\share\\\\revoked.json"

**Revoke multiple signatures at once (comma-separated):**

.. code-block:: bash

    python tools/licensing/sign_revocation.py \\
        --add "sig1,sig2,sig3" \\
        --private-key keys/ed25519_private.key \\
        --out "\\\\\\\\server\\\\share\\\\revoked.json"

**How to get the signature to revoke:**
Open the customer's ``.lic`` file and copy the value of the ``"signature"``
field verbatim.

Output format
-------------
The tool writes a JSON file with three fields::

    {
        "issued_at":    "2026-02-18T12:00:00+00:00",
        "revoked":      ["sig1", "sig2"],
        "rl_signature": "base64url-Ed25519-sig"
    }

The ``"rl_signature"`` covers the canonical form of ``{"issued_at": ...,
"revoked": [...]}`` — identical algorithm to license signing.  Any user who
edits ``"revoked"`` without re-signing will invalidate ``"rl_signature"``,
and the app will ignore the tampered file.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def sign_revocation(
    revoked_signatures: list[str],
    private_key_path: Path,
    out_path: Path,
    existing_path: Path | None = None,
) -> None:
    """Build and write a signed ``revoked.json`` file.

    If *existing_path* is provided, the existing ``"revoked"`` list is read
    and merged with *revoked_signatures* (duplicates removed).  The result
    is re-signed with a fresh ``"issued_at"`` timestamp.

    Args:
        revoked_signatures: New signature strings to add to the revoked list.
        private_key_path: Path to the hex-encoded Ed25519 private key file
            produced by ``keygen.py``.
        out_path: Destination path for the signed ``revoked.json`` file.
        existing_path: Optional path to an existing ``revoked.json`` to
            merge with.  The existing file's ``"revoked"`` list is loaded
            (its old ``"rl_signature"`` is discarded).

    Raises:
        FileNotFoundError: If *private_key_path* does not exist.
        ValueError: If the private key file is malformed.
        OSError: If *out_path* cannot be written.
        RuntimeError: If the ``cryptography`` package is not installed.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required. "
            "Install it with: pip install cryptography"
        ) from exc

    # Reuse canonical_serialize — guarantees byte-for-byte match with the
    # verification path in revocation.py.
    try:
        from core.licensing.crypto import canonical_serialize
    except ImportError:
        # Fallback for machines without the full CreoVCS source tree.
        def canonical_serialize(data: dict) -> bytes:  # type: ignore[misc]
            return json.dumps(
                data,
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=True,
            ).encode('utf-8')

    # ---- Load private key -------------------------------------------------
    private_key_path = Path(private_key_path)
    try:
        priv_hex = private_key_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Private key file not found: '{private_key_path}'."
        )

    try:
        priv_bytes = bytes.fromhex(priv_hex)
    except ValueError as exc:
        raise ValueError(
            f"Private key file is not valid hex: {exc}"
        ) from exc

    if len(priv_bytes) != 32:
        raise ValueError(
            f"Private key must be 32 bytes (got {len(priv_bytes)})."
        )

    try:
        private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Could not load private key: {exc}") from exc

    # ---- Merge with existing list (if provided) --------------------------
    existing_revoked: list[str] = []
    if existing_path is not None:
        existing_path = Path(existing_path)
        if existing_path.exists():
            try:
                data = json.loads(existing_path.read_text(encoding="utf-8"))
                existing_revoked = [
                    str(s) for s in data.get("revoked", [])
                    if isinstance(s, str)
                ]
            except (OSError, json.JSONDecodeError):
                print(
                    f"Warning: could not read existing file '{existing_path}' "
                    "— starting with an empty list.",
                    file=sys.stderr,
                )

    # Merge and deduplicate while preserving insertion order.
    merged: dict[str, None] = dict.fromkeys(existing_revoked)
    for sig in revoked_signatures:
        sig = sig.strip()
        if sig:
            merged[sig] = None
    final_revoked = list(merged.keys())

    # ---- Build payload and sign ------------------------------------------
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    payload_dict = {
        "issued_at": issued_at,
        "revoked": final_revoked,
    }
    canonical_bytes = canonical_serialize(payload_dict)
    sig_raw: bytes = private_key.sign(canonical_bytes)
    rl_signature = base64.urlsafe_b64encode(sig_raw).rstrip(b"=").decode("ascii")

    # ---- Write output file -----------------------------------------------
    out_data = {
        "issued_at": issued_at,
        "revoked": final_revoked,
        "rl_signature": rl_signature,
    }
    out_path = Path(out_path)
    out_path.write_text(
        json.dumps(out_data, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or update a signed CreoVCS revocation list (revoked.json).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Start a new list:\n"
            "  python tools/licensing/sign_revocation.py \\\n"
            "      --add \"<signature>\" \\\n"
            "      --private-key keys/ed25519_private.key \\\n"
            "      --out \\\\\\\\server\\\\share\\\\revoked.json\n\n"
            "  # Append to an existing list:\n"
            "  python tools/licensing/sign_revocation.py \\\n"
            "      --add \"<signature>\" \\\n"
            "      --existing \\\\\\\\server\\\\share\\\\revoked.json \\\n"
            "      --private-key keys/ed25519_private.key \\\n"
            "      --out \\\\\\\\server\\\\share\\\\revoked.json\n"
        ),
    )
    parser.add_argument(
        "--add",
        required=True,
        metavar="SIG[,SIG...]",
        help=(
            'Comma-separated license signature string(s) to revoke. '
            'Copy the "signature" field from the customer\'s .lic file.'
        ),
    )
    parser.add_argument(
        "--existing",
        metavar="PATH",
        default=None,
        help="Path to an existing revoked.json to merge with (optional).",
    )
    parser.add_argument(
        "--private-key",
        required=True,
        metavar="KEY",
        help="Path to the hex-encoded Ed25519 private key file (from keygen.py).",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="Destination path for the signed revoked.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the sign_revocation command-line tool."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    new_sigs = [s.strip() for s in args.add.split(",") if s.strip()]
    if not new_sigs:
        print("ERROR: --add requires at least one non-empty signature.", file=sys.stderr)
        return 1

    print(f"Revoking {len(new_sigs)} signature(s) ...")
    try:
        sign_revocation(
            revoked_signatures=new_sigs,
            private_key_path=Path(args.private_key),
            out_path=Path(args.out),
            existing_path=Path(args.existing) if args.existing else None,
        )
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Print summary.
    try:
        data = json.loads(Path(args.out).read_text(encoding="utf-8"))
        print(f"Signed revocation list written to '{args.out}'.")
        print(f"  Issued at : {data.get('issued_at')}")
        print(f"  Total revoked entries: {len(data.get('revoked', []))}")
        for sig in data.get("revoked", []):
            indicator = " <-- NEW" if sig in new_sigs else ""
            print(f"    - {sig[:40]}...{indicator}")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
