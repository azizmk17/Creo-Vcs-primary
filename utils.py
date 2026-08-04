"""utils.py
----------
File-system and Creo-versioned-file helpers used across services and pages.
"""

import os
import shutil
import hashlib
import tempfile
import subprocess


def long_path(path: str) -> str:
    r"""Return a Windows extended-length path for filesystem APIs.

    Windows may fail around 260 characters unless paths use the ``\\?\`` prefix.
    The prefix requires an absolute path. UNC paths become ``\\?\UNC\...``.
    Non-Windows paths are returned unchanged.
    """
    if not path or os.name != "nt":
        return path
    normalized = os.path.normpath(str(path))
    if normalized.startswith("\\\\?\\"):
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized.lstrip("\\")
    return "\\\\?\\" + os.path.abspath(normalized)


def safe_exists(path: str) -> bool:
    return bool(path and os.path.exists(long_path(path)))


def safe_isdir(path: str) -> bool:
    return bool(path and os.path.isdir(long_path(path)))


def safe_isfile(path: str) -> bool:
    return bool(path and os.path.isfile(long_path(path)))


def safe_listdir(path: str):
    return os.listdir(long_path(path))


def safe_copy2(src: str, dst: str):
    ensure_dir_exists(os.path.dirname(dst))
    return shutil.copy2(long_path(src), long_path(dst))


def safe_move(src: str, dst: str):
    ensure_dir_exists(os.path.dirname(dst))
    return shutil.move(long_path(src), long_path(dst))


def safe_remove(path: str) -> None:
    os.remove(long_path(path))


def safe_rmtree(path: str) -> None:
    shutil.rmtree(long_path(path))


def safe_open(path: str, *args, **kwargs):
    return open(long_path(path), *args, **kwargs)


def safe_getsize(path: str) -> int:
    """Return a file size while supporting Windows extended-length paths."""
    return os.path.getsize(long_path(path))


def _short_view_copy(path: str) -> str:
    """Copy a long-path file to a short temp path for apps that cannot open long paths."""
    source = os.path.abspath(os.path.normpath(str(path)))
    stem, ext = os.path.splitext(os.path.basename(source))
    digest = hashlib.sha1(source.encode("utf-8", errors="ignore")).hexdigest()[:10]
    safe_stem = "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in stem).strip()
    if len(safe_stem) > 80:
        safe_stem = safe_stem[:80].rstrip(" ._-")
    safe_name = f"{safe_stem or 'document'}_{digest}{ext}"
    target_dir = os.path.join(tempfile.gettempdir(), "CreoVCS_open")
    ensure_dir_exists(target_dir)
    target = os.path.join(target_dir, safe_name)
    if not safe_exists(target) or os.path.getmtime(long_path(source)) > os.path.getmtime(long_path(target)):
        safe_copy2(source, target)
    return target


def _short_dir_link(path: str) -> str:
    """Return a short directory link for browsing a very long directory path."""
    target = os.path.abspath(os.path.normpath(str(path)))
    digest = hashlib.sha1(target.encode("utf-8", errors="ignore")).hexdigest()[:12]
    link_root = os.path.join(tempfile.gettempdir(), "CreoVCS_dirs")
    ensure_dir_exists(link_root)
    link_path = os.path.join(link_root, f"dir_{digest}")
    if safe_isdir(link_path):
        return link_path
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", link_path, target],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return target
    return link_path


def safe_startfile(path: str) -> None:
    """Open a file/folder on Windows while supporting extended-length paths."""
    if os.name == "nt":
        normalized = os.path.abspath(os.path.normpath(str(path)))
        if safe_isfile(normalized) and len(normalized) >= 240:
            os.startfile(_short_view_copy(normalized))
            return
        if safe_isdir(normalized) and len(normalized) >= 240:
            os.startfile(_short_dir_link(normalized))
            return
        try:
            os.startfile(normalized)
        except OSError:
            if safe_isfile(normalized):
                os.startfile(_short_view_copy(normalized))
                return
            os.startfile(long_path(normalized))
    else:
        os.startfile(path)

#
##---------------------------------------------------------------------------
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
        for f in safe_listdir(working_dir):
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
    if directory:
        os.makedirs(long_path(directory), exist_ok=True)
