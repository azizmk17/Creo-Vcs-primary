"""
tools/licensing/keygen.py
=========================
Offline Ed25519 key pair generator for CreoVCS license signing.

Run this script **once** on a secure, air-gapped machine to create the
vendor signing key pair.  Store the private key in a secrets manager
(e.g. HashiCorp Vault, AWS Secrets Manager) and **never** commit it to
version control.  Embed the public key in the application build via your
CI/CD pipeline (e.g. as an environment variable that gets baked into a
``build_constants.py`` file that is excluded from source control via
``.gitignore``).

Key format
----------
Both keys are written as lowercase hex-encoded strings — one line per file.
The private key file contains the raw 32-byte Ed25519 seed (not the full
64-byte expanded key).  The public key file contains the raw 32-byte public
key.  This compact format is compatible with ``cryptography``'s
``Ed25519PrivateKey.from_private_bytes()`` and
``Ed25519PublicKey.from_public_bytes()``.

Usage
-----
.. code-block:: bash

    python tools/licensing/keygen.py --out-dir ./keys

    # Outputs:
    #   keys/ed25519_private.key   (32 bytes, hex-encoded)
    #   keys/ed25519_public.key    (32 bytes, hex-encoded)

    # Then read the public key for embedding:
    cat keys/ed25519_public.key
    # e.g. a3f1c2...  (64 hex chars = 32 bytes)

Security reminders
------------------
* Do NOT commit ``ed25519_private.key`` to version control.
* Add ``*.key`` or ``keys/`` to ``.gitignore``.
* Treat the private key file with the same care as an SSH private key.
* If the private key is compromised, generate a new key pair and re-sign
  all customer licenses with the new key.  The new public key must be
  distributed in a new application build.

Dependencies
------------
Only the ``cryptography`` package is required (same as the runtime module).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def generate_keypair(out_dir: Path) -> tuple[bytes, bytes]:
    """Generate an Ed25519 key pair and write the key files to *out_dir*.

    The directory is created if it does not already exist.  Existing key
    files are **not** overwritten — the function aborts with an error if
    either output file is already present, preventing accidental key
    rotation without deliberate intent.

    Args:
        out_dir: The directory in which to write ``ed25519_private.key``
            and ``ed25519_public.key``.

    Returns:
        A tuple of ``(private_key_bytes, public_key_bytes)``, each 32 bytes.
        These are the raw key materials, not the hex-encoded strings written
        to disk.

    Raises:
        FileExistsError: If either output file already exists.
        OSError: If the output directory cannot be created or the files
            cannot be written.
        RuntimeError: If the ``cryptography`` package is not installed.

    Example::

        priv, pub = generate_keypair(Path("./keys"))
        # keys/ed25519_private.key and keys/ed25519_public.key are now on disk.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required. "
            "Install it with: pip install cryptography"
        ) from exc

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    priv_path = out_dir / "ed25519_private.key"
    pub_path = out_dir / "ed25519_public.key"

    # Guard against overwriting existing keys.
    for p in (priv_path, pub_path):
        if p.exists():
            raise FileExistsError(
                f"Key file already exists: '{p}'. "
                "Delete it manually if you intend to rotate keys."
            )

    # Generate the key pair.
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_bytes: bytes = private_key.private_bytes_raw()  # 32-byte seed
    pub_bytes: bytes = public_key.public_bytes_raw()     # 32-byte public key

    # Write hex-encoded keys.
    priv_path.write_text(priv_bytes.hex() + "\n", encoding="ascii")
    pub_path.write_text(pub_bytes.hex() + "\n", encoding="ascii")

    # Set restrictive permissions on the private key (Unix-like systems only).
    try:
        import os
        os.chmod(priv_path, 0o600)
    except (AttributeError, NotImplementedError, OSError):
        # chmod not available on Windows — continue without failing.
        pass

    return priv_bytes, pub_bytes


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an Ed25519 key pair for CreoVCS license signing.\n\n"
            "Store the private key securely. Embed the public key in your build."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python tools/licensing/keygen.py --out-dir ./keys\n\n"
            "After generation, embed the public key in your application:\n"
            "  cat keys/ed25519_public.key\n"
        ),
    )
    parser.add_argument(
        "--out-dir",
        default="./keys",
        metavar="DIR",
        help="Directory in which to write the key files (default: ./keys).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the keygen command-line tool.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)

    print(f"Generating Ed25519 key pair in '{out_dir}' ...")
    try:
        priv_bytes, pub_bytes = generate_keypair(out_dir)
    except FileExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    priv_path = out_dir / "ed25519_private.key"
    pub_path = out_dir / "ed25519_public.key"

    print()
    print(f"  Private key : {priv_path}")
    print(f"  Public key  : {pub_path}")
    print()
    print("Public key hex (embed this in your build):")
    print(f"  {pub_bytes.hex()}")
    print()
    print("SECURITY REMINDER:")
    print("  * Do NOT commit the private key to version control.")
    print("  * Add '*.key' or the 'keys/' directory to .gitignore.")
    print("  * Store the private key in a secrets manager.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
