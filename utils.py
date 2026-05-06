"""utils.py
----------
File-system and Creo-versioned-file helpers used across services and pages.
"""

import os

#
# ---------------------------------------------------------------------------
# Creo versioned-file helpers
# ---------------------------------------------------------------------------

def is_creo_file(filename: str) -> bool:
    """Return True if *filename* looks like a Creo versioned file.

    Valid pattern:  <name>.<ext>.<version>
    where <ext> is one of  prt | asm | drw  and <version> is a digit string.
    Examples: part1.prt.3, drawing.drw.12, assembly.asm.1
    """
    parts = filename.split(".")
    return (
        len(parts) >= 3
        and parts[-1].isdigit()
        and parts[-2] in ("prt", "asm", "drw")
    )


def get_base_name(filename: str):
    """Return the base name (without the version number) of a Creo file.

    Returns None if *filename* is not a valid Creo versioned file.
    Example: 'part1.prt.3' → 'part1.prt'
    """
    if not is_creo_file(filename):
        return None
    return ".".join(filename.split(".")[:-1])


def get_version_number(filename: str):
    """Return the integer version number of a Creo file.

    Returns None if *filename* is not a valid Creo versioned file.
    Example: 'part1.prt.3' → 3
    """
    if not is_creo_file(filename):
        return None
    return int(filename.split(".")[-1])


def get_next_version_number(working_dir: str, base_name: str) -> int:
    """Return the next available version number for *base_name* in *working_dir*.

    Scans all Creo versioned files whose base name matches and returns
    max(existing_versions) + 1, or 1 if none exist.
    """
    max_version = 0
    try:
        for f in os.listdir(working_dir):
            if f.startswith(base_name + ".") and is_creo_file(f):
                v = get_version_number(f)
                if v is not None and v > max_version:
                    max_version = v
    except OSError:
        pass
    return max_version + 1


# ---------------------------------------------------------------------------
# Directory helper
# ---------------------------------------------------------------------------

def ensure_dir_exists(directory: str) -> None:
    """Create *directory* (and any missing parents) if it does not exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)
