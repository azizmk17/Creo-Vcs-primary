"""Release trust is compiled into managed builds, never loaded from a feed."""
import os
import sys


def release_public_key():
    try:
        from nexus_release_trust import PUBLIC_KEY_HEX
        value = PUBLIC_KEY_HEX
    except ImportError:
        value = "" if getattr(sys, "frozen", False) else os.environ.get("NEXUS_RELEASE_PUBLIC_KEY", "")
    value = str(value).strip()
    if not value:
        return ""
    if len(bytes.fromhex(value)) != 32:
        raise ValueError("The release public key must contain 32 bytes.")
    return value.lower()
