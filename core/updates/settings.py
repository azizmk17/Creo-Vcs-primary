"""Per-user update preferences. Signing keys are never stored here."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def managed_root():
    value = os.environ.get("NEXUS_INSTALL_ROOT", "").strip()
    return Path(value).resolve() if value else None


def settings_path():
    return Path(os.environ.get("APPDATA") or Path.home()) / "Nexus" / "updates.json"


def read_settings():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _mutate(operation):
    # Checks in Nexus and an updater can finish at the same time. Keep the
    # sequence high-water mark and feed preferences in one locked transaction.
    from .install import _lock

    destination = settings_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _lock(destination.with_suffix(".lock"), timeout=5):
        data = read_settings()
        operation(data)
        descriptor, temporary = tempfile.mkstemp(prefix="updates-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(6):
                try:
                    os.replace(temporary, destination)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.05)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return data


def save_settings(**changes):
    return _mutate(lambda data: data.update(changes))


def update_feed():
    root = managed_root()
    if root:
        try:
            data = json.loads((root / "install.json").read_text(encoding="utf-8"))
            if data.get("feed"):
                return str(data["feed"])
        except (OSError, ValueError, AttributeError):
            pass
    return str(read_settings().get("feed") or "")


def last_sequence(public_key):
    identity = hashlib.sha256(public_key.encode("ascii")).hexdigest()
    return int(read_settings().get("sequences", {}).get(identity, 0))


def remember_sequence(public_key, sequence):
    identity = hashlib.sha256(public_key.encode("ascii")).hexdigest()
    def update(data):
        sequences = dict(data.get("sequences") or {})
        sequences[identity] = max(int(sequences.get(identity, 0)), int(sequence))
        data["sequences"] = sequences
    _mutate(update)
