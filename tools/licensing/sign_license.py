"""
tools/licensing/sign_license.py
================================
Offline license signing tool for CreoVCS.

This script takes a JSON license template (without a signature) and a private
key file produced by ``keygen.py``, signs the canonical form of the template
with Ed25519, and writes a deployable ``.lic`` file.

Run this script on a secure machine that has access to the private key.
The resulting ``.lic`` file is safe to distribute to the customer.

License template format
-----------------------
The template is a JSON file containing all license fields **except**
``"signature"`` (the script adds that field)::

    {
        "product":    "CreoVCS",
        "licensee":   "Acme Engineering SAS",
        "issued_at":  "2026-02-18T00:00:00+00:00",
        "expires_at": "2027-02-18T00:00:00+00:00",
        "features":   [
            "bom_management",
            "commit",
            "snapshot",
            "advanced_diff",
            "baseline",
            "package_export"
        ],
        "version_constraint": ">=4.0,<5.0",
        "machine_id": []
    }

Field notes
~~~~~~~~~~~
* ``"expires_at"``: Set to ``null`` for a perpetual (never-expiring) license.
* ``"machine_id"``: Set to an empty list ``[]`` for a floating license valid
  on any machine.  Set to a list of machine ID hex strings for a node-locked
  license.  Get a machine's ID by running::

      python -c "from core.licensing.machine import get_machine_id; print(get_machine_id())"

  on the target machine.

Usage
-----
.. code-block:: bash

    python tools/licensing/sign_license.py \\
        --template license_template.json \\
        --private-key keys/ed25519_private.key \\
        --out acme_engineering.lic

    # Verify the output (optional):
    python -c "
    import os, json
    from pathlib import Path
    from core.licensing.loader import load_license
    pub = bytes.fromhex(open('keys/ed25519_public.key').read().strip())
    p = load_license(Path('acme_engineering.lic'), pub)
    print('OK — licensed to:', p.licensee)
    "

Output format
-------------
The ``.lic`` file is pretty-printed JSON with the ``"signature"`` field
appended last.  Note that the *canonical* form used for signing is the
compact (no-whitespace, sorted-keys) form — the pretty-printed output is
only for human readability.  The verifier reconstructs the canonical form
from the parsed JSON, so the output formatting does not affect verification.

Dependencies
------------
Only the ``cryptography`` package (same as the runtime module).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any


def sign_license(
    template_path: Path,
    private_key_path: Path,
    out_path: Path,
) -> None:
    """Sign a license template and write a deployable ``.lic`` file.

    The signing process:

    1. Reads and JSON-parses the template file.
    2. Validates that the template does NOT already contain a ``"signature"``
       key (to prevent accidentally double-signing).
    3. Validates that all required fields are present.
    4. Produces the canonical byte string via ``canonical_serialize`` (the
       same function used at verification time).
    5. Signs the canonical bytes with the Ed25519 private key.
    6. Base64url-encodes the 64-byte signature (without padding).
    7. Adds the ``"signature"`` field to the template dict.
    8. Writes the complete dict as pretty-printed JSON to *out_path*.

    Args:
        template_path: Path to the unsigned JSON template file.
        private_key_path: Path to the 32-byte hex-encoded private key file
            produced by ``keygen.py``.
        out_path: Destination path for the signed ``.lic`` file.
            Parent directory must already exist.

    Raises:
        FileNotFoundError: If *template_path* or *private_key_path* does
            not exist.
        ValueError: If the template is missing required fields, already
            contains a ``"signature"`` key, or the private key file is
            malformed.
        json.JSONDecodeError: If *template_path* is not valid JSON.
        OSError: If *out_path* cannot be written.
        RuntimeError: If the ``cryptography`` package is not installed.

    Example::

        sign_license(
            template_path=Path("license_template.json"),
            private_key_path=Path("keys/ed25519_private.key"),
            out_path=Path("customer.lic"),
        )
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required. "
            "Install it with: pip install cryptography"
        ) from exc

    # We reuse the same canonical_serialize function from the runtime module
    # to guarantee byte-for-byte identical output on the signing and
    # verification paths.
    #
    # This import works when the script is run from the project root:
    #   python tools/licensing/sign_license.py ...
    # For runs from other directories, add the project root to PYTHONPATH.
    try:
        from core.licensing.crypto import canonical_serialize
    except ImportError:
        # Fallback: inline the algorithm so this script can also be run on a
        # machine that does not have the full CreoVCS source tree installed,
        # as long as the cryptography package is available.
        def canonical_serialize(data: dict) -> bytes:  # type: ignore[misc]
            return json.dumps(
                data,
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=True,
            ).encode('utf-8')

    # ------------------------------------------------------------------ #
    # Read and parse the template
    # ------------------------------------------------------------------ #
    template_path = Path(template_path)
    try:
        raw_text = template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"Template file not found: '{template_path}'.")

    template: dict[str, Any] = json.loads(raw_text)

    if not isinstance(template, dict):
        raise ValueError("Template must be a JSON object at the top level.")

    # ------------------------------------------------------------------ #
    # Validate template
    # ------------------------------------------------------------------ #
    if "signature" in template:
        raise ValueError(
            "The template already contains a 'signature' field. "
            "Remove it before signing."
        )

    required = ("product", "licensee", "issued_at", "features")
    missing = [f for f in required if f not in template]
    if missing:
        raise ValueError(
            "Template is missing required field(s): "
            + ", ".join(f"'{f}'" for f in missing)
            + "."
        )

    if template.get("product") != "CreoVCS":
        raise ValueError(
            f"Template 'product' must be 'CreoVCS', got '{template.get('product')}'."
        )

    # ------------------------------------------------------------------ #
    # Load the private key
    # ------------------------------------------------------------------ #
    private_key_path = Path(private_key_path)
    try:
        priv_hex = private_key_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raise FileNotFoundError(f"Private key file not found: '{private_key_path}'.")

    try:
        priv_bytes = bytes.fromhex(priv_hex)
    except ValueError as exc:
        raise ValueError(
            f"Private key file '{private_key_path}' is not valid hex: {exc}"
        ) from exc

    if len(priv_bytes) != 32:
        raise ValueError(
            f"Private key must be 32 bytes (got {len(priv_bytes)}). "
            "Ensure the key was generated by keygen.py."
        )

    try:
        private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Could not load private key: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Sign
    # ------------------------------------------------------------------ #
    canonical_bytes = canonical_serialize(template)
    sig_raw: bytes = private_key.sign(canonical_bytes)         # 64 bytes
    sig_b64url: str = base64.urlsafe_b64encode(sig_raw).rstrip(b"=").decode("ascii")

    # ------------------------------------------------------------------ #
    # Write the .lic file
    # ------------------------------------------------------------------ #
    lic_data = dict(template)  # shallow copy to preserve original order
    lic_data["signature"] = sig_b64url

    out_path = Path(out_path)
    out_path.write_text(
        json.dumps(lic_data, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sign a CreoVCS license template and produce a deployable .lic file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python tools/licensing/sign_license.py \\\n"
            "      --template license_template.json \\\n"
            "      --private-key keys/ed25519_private.key \\\n"
            "      --out customer.lic\n"
        ),
    )
    parser.add_argument(
        "--template",
        required=True,
        metavar="JSON",
        help="Path to the unsigned license template JSON file.",
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
        metavar="LIC",
        help="Destination path for the signed .lic output file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the sign_license command-line tool.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    template_path = Path(args.template)
    private_key_path = Path(args.private_key)
    out_path = Path(args.out)

    print(f"Signing license template '{template_path}' ...")
    try:
        sign_license(template_path, private_key_path, out_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Signed license written to '{out_path}'.")

    # Print a human-readable summary of what was signed.
    try:
        lic_data = json.loads(out_path.read_text(encoding="utf-8"))
        print()
        print("License summary:")
        print(f"  Product   : {lic_data.get('product', '?')}")
        print(f"  Licensee  : {lic_data.get('licensee', '?')}")
        print(f"  Issued    : {lic_data.get('issued_at', '?')}")
        print(f"  Expires   : {lic_data.get('expires_at') or 'Never (perpetual)'}")
        print(f"  Features  : {', '.join(lic_data.get('features', []))}")
        machine_ids = lic_data.get("machine_id") or []
        print(f"  Machines  : {'Any (floating)' if not machine_ids else ', '.join(machine_ids)}")
    except Exception:
        pass  # Summary is informational; don't fail on display errors.

    return 0


if __name__ == "__main__":
    sys.exit(main())
