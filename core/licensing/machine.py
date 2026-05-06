"""
core/licensing/machine.py
=========================
Cross-platform machine identity for per-machine license binding.

``get_machine_id()`` returns a stable, opaque 32-character hex string derived
from a platform-specific hardware or OS identity source.  The string is used
by ``core.licensing.checks.check_machine`` to verify that the running machine
is in the license's allowed list.

Stability guarantees
--------------------
The identifier is stable across:

* Reboots
* Network interface changes (VPN, Docker bridge interfaces)
* Python version upgrades
* Application reinstalls

The identifier changes (intentionally) if:

* The OS is reinstalled (Windows MachineGuid is regenerated; /etc/machine-id
  is regenerated on Linux).
* On macOS, the IOPlatformUUID changes when the system board is replaced.

Why not ``uuid.getnode()``?
~~~~~~~~~~~~~~~~~~~~~~~~~~~
``uuid.getnode()`` uses the MAC address of the first non-loopback network
interface.  MAC addresses change with VPN adapters, Docker bridges, and NIC
replacements.  This makes them unsuitable as a stable machine fingerprint.

Why not the CPU serial number?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Reading the CPU serial requires privileged access on most modern operating
systems and is not universally available.

Obfuscation readiness
---------------------
This module uses only the Python standard library and makes no dynamic
``__import__``, ``eval``, or ``inspect`` calls.  It is suitable for
compilation with Cython or Nuitka without modification.

Platform strategy
-----------------
+----------+-----------------------------------------------------------------+
| Platform | Source                                                          |
+==========+=================================================================+
| Windows  | ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``        |
|          | Stable OS-level UUID; set at Windows installation time.         |
+----------+-----------------------------------------------------------------+
| Linux    | ``/etc/machine-id`` (systemd) or ``/var/lib/dbus/machine-id``   |
|          | Generated at OS install time; requires no elevated privileges.  |
+----------+-----------------------------------------------------------------+
| macOS    | ``IOPlatformUUID`` from ``ioreg``                               |
|          | Hardware-level UUID tied to the system board.                   |
+----------+-----------------------------------------------------------------+
| Fallback | SHA-256 of ``platform.node() + str(uuid.getnode())``            |
|          | Always available; less stable but better than nothing.          |
+----------+-----------------------------------------------------------------+

The raw string from each source is SHA-256 hashed and the first 32 hex
characters are returned.  Hashing provides a uniform output length and
prevents the raw OS identity from being extracted by inspecting memory.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import uuid


def get_machine_id() -> str:
    """Return a stable, cross-platform machine identifier.

    The identifier is a 32-character lowercase hex string derived from a
    platform-specific hardware or OS identity source, SHA-256 hashed and
    truncated to 32 characters.

    The function never raises.  If the preferred platform source is
    unavailable, it falls back through progressively less stable sources,
    ultimately always producing a non-empty string.

    Returns:
        A 32-character lowercase hex string suitable for inclusion in a
        license ``machine_id`` list.

    Example::

        from core.licensing.machine import get_machine_id
        mid = get_machine_id()
        print(mid)  # e.g. "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    """
    raw: str = _read_platform_id()
    return _hash_to_hex(raw)


# ---------------------------------------------------------------------------
# Internal helpers — not part of the public API
# ---------------------------------------------------------------------------

def _hash_to_hex(raw: str) -> str:
    """SHA-256 hash *raw*, return first 32 hex characters (lowercase)."""
    digest = hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()
    return digest[:32]


def _read_platform_id() -> str:
    """Attempt each platform strategy in order; return the first success."""
    system = platform.system()

    if system == "Windows":
        raw = _windows_machine_guid()
        if raw:
            return raw

    elif system == "Linux":
        raw = _linux_machine_id()
        if raw:
            return raw

    elif system == "Darwin":
        raw = _macos_platform_uuid()
        if raw:
            return raw

    # Universal fallback — always succeeds.
    return _fallback_id()


def _windows_machine_guid() -> str:
    """Read MachineGuid from the Windows registry.

    Returns:
        The raw registry value string, or an empty string on any error.
    """
    try:
        import winreg  # Available only on Windows; import kept local for portability.
        key_path = r"SOFTWARE\Microsoft\Cryptography"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except Exception:
        return ""


def _linux_machine_id() -> str:
    """Read the systemd machine-id from the standard locations.

    Returns:
        The first non-empty line of the machine-id file, or an empty string.
    """
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                line = fh.readline().strip()
                if line:
                    return line
        except OSError:
            continue
    return ""


def _macos_platform_uuid() -> str:
    """Read IOPlatformUUID via ioreg on macOS.

    Returns:
        The UUID string, or an empty string on any error.
    """
    try:
        result = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "IOPlatformUUID" in line:
                # Line format: "IOPlatformUUID" = "XXXXXXXX-..."
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"')
    except Exception:
        pass
    return ""


def _fallback_id() -> str:
    """Produce a best-effort machine ID using standard library primitives.

    Combines the hostname (``platform.node()``) with the hardware address
    from ``uuid.getnode()``.  Less stable than OS-level IDs (changes with
    NIC changes) but always available and always non-empty.

    Returns:
        A string suitable for hashing.
    """
    node = platform.node()
    mac = str(uuid.getnode())
    return f"{node}:{mac}"
