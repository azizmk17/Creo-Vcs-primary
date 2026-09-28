"""Small header checks for files that claim to be native Creo documents."""

import re


_HEADER_BY_EXTENSION = {"prt": b"PART", "asm": b"ASSEMBLY", "drw": b"DRAWING"}
_HEADER = re.compile(rb"^#UGC:\d+\s+(PART|ASSEMBLY|DRAWING)\b")


def is_native_creo_file(path, extension=None):
    """Return whether the Creo file header matches its .prt/.asm/.drw extension."""
    extension = str(extension or "").strip().casefold().lstrip(".")
    if extension not in _HEADER_BY_EXTENSION:
        return False
    try:
        with open(path, "rb") as stream:
            header = stream.read(128)
    except OSError:
        return False
    match = _HEADER.match(header)
    return bool(match and match.group(1) == _HEADER_BY_EXTENSION[extension])
