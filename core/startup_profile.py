"""Opt-in startup timestamps. No database access, authentication, or Qt imports."""
import json
import math
import os
from pathlib import Path
import sys
import time

_TARGET = os.environ.get("NEXUS_STARTUP_PROFILE_FILE", "")
try:
    _ORIGIN = float(os.environ.get("NEXUS_STARTUP_PROFILE_ORIGIN", time.perf_counter()))
    if not math.isfinite(_ORIGIN) or _ORIGIN > time.perf_counter():
        _ORIGIN = time.perf_counter()
except ValueError:
    _ORIGIN = time.perf_counter()
_EVENTS = []


def enabled():
    return bool(_TARGET)


def mark(name, *, flush=False, **details):
    if not _TARGET:
        return
    if len(_EVENTS) < 512:
        _EVENTS.append(dict(stage=name, seconds=round(time.perf_counter() - _ORIGIN, 6), **details))
    if flush or os.environ.get("NEXUS_STARTUP_PROFILE_EACH_STAGE") == "1":
        save()


def save():
    if not _TARGET:
        return
    try:
        config = sys.modules.get("config")
        data = {
            "version": getattr(config, "APP_VERSION", None),
            "frozen": bool(getattr(sys, "frozen", False)),
            "events": _EVENTS,
            "loaded_optional_modules": [name for name in ("fitz", "pymupdf", "openpyxl", "OCC") if name in sys.modules],
        }
        target = Path(_TARGET)
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_name(target.name + ".tmp")
        pending.write_text(json.dumps(data, indent=2), encoding="utf-8")
        pending.replace(target)
    except (OSError, ValueError):
        # Profiling must never stop or change the actual application workflow.
        pass


def stop_at_login():
    """A measurement may close the login window; it cannot authenticate a user."""
    return enabled() and os.environ.get("NEXUS_STARTUP_PROFILE_EXIT_AT_LOGIN") == "1"
