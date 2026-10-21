"""Create current-user shortcuts to the managed installation's stable launcher."""
from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


class ShortcutError(RuntimeError):
    """Windows could not create an application shortcut."""


# Windows Known Folders honor redirected, localized, and OneDrive directories.
_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
_PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"


def _known_folder(identifier: str) -> Path:
    if os.name != "nt":
        raise ShortcutError("Nexus shortcuts can only be installed on Windows.")
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    guid = GUID.from_buffer_copy(uuid.UUID(identifier).bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                         wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    pointer = ctypes.c_void_p()
    try:
        result = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer))
        if result < 0 or not pointer.value:
            raise ShortcutError("Windows could not find your Desktop or Start menu folder.")
        return Path(ctypes.wstring_at(pointer.value))
    finally:
        if pointer.value:
            ole.CoTaskMemFree(pointer)


def _literal(value: Path | str) -> str:
    """A PowerShell single-quoted literal, including apostrophes and Unicode."""
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str) -> None:
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=True, capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _write_shortcut(launcher: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Save to a nonexistent .lnk before replacing an existing shortcut atomically.
    with tempfile.TemporaryDirectory(prefix=".nexus-shortcut-", dir=destination.parent) as directory:
        temporary = Path(directory) / "Nexus.lnk"
        script = f"""$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
try {{
    if (Test-Path -LiteralPath {_literal(destination)}) {{
        $existing = $shell.CreateShortcut({_literal(destination)})
        if (-not [string]::Equals($existing.TargetPath, {_literal(launcher)}, [System.StringComparison]::OrdinalIgnoreCase)) {{
            throw 'A different shortcut named Nexus already exists. Rename it before creating the Nexus shortcut.'
        }}
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($existing)
    }}
    $link = $shell.CreateShortcut({_literal(temporary)})
    $link.TargetPath = {_literal(launcher)}
    $link.WorkingDirectory = {_literal(launcher.parent)}
    $link.Description = 'Launch Nexus'
    $link.Save()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($link)
}} finally {{
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
}}
"""
        _run_powershell(script)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ShortcutError("Windows did not create the Nexus shortcut.")
        os.replace(temporary, destination)


def create_shortcuts(root: Path | str, *, desktop: bool = True,
                     desktop_dir: Path | str | None = None,
                     programs_dir: Path | str | None = None) -> list[Path]:
    """Create Start menu and optional Desktop links without administrator rights.

    Destination overrides are for tests and managed deployment; ordinary setup
    resolves the actual current-user Known Folders through Windows.
    """
    launcher = Path(root).resolve() / "bootstrap/NexusLauncher.exe"
    if not launcher.is_file():
        raise ShortcutError("The installed Nexus launcher is missing. Run Nexus Setup again.")
    destinations = [
        (Path(programs_dir).resolve() if programs_dir is not None else _known_folder(_PROGRAMS))
        / "Nexus/Nexus.lnk"
    ]
    if desktop:
        destinations.append(
            (Path(desktop_dir).resolve() if desktop_dir is not None else _known_folder(_DESKTOP))
            / "Nexus.lnk"
        )
    created = []
    for destination in destinations:
        try:
            _write_shortcut(launcher, destination)
        except ShortcutError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise ShortcutError(
                f"Nexus is installed, but Windows could not create the shortcut at {destination}. "
                "You can launch Nexus using bootstrap\\NexusLauncher.exe in the installation folder."
            ) from error
        created.append(destination)
    return created
