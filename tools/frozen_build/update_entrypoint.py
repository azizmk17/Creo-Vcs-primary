"""Small independently packaged launcher/updater; contains no OCC or PDF runtime."""
import json
from pathlib import Path
import sys

from core.updates.desktop import main
from core.updates.install import UPDATER_VERSION
from core.updates.trust import release_public_key


if __name__ == "__main__":
    public = release_public_key()
    if "--updater-self-test" in sys.argv:
        destination = Path(sys.argv[sys.argv.index("--updater-self-test") + 1])
        destination.write_text(json.dumps({"ok": bool(public), "updater_version": UPDATER_VERSION,
                                           "release_public_key": public, "frozen": bool(getattr(sys, "frozen", False))}), encoding="utf-8")
    else:
        role = "updater" if Path(sys.executable).stem.casefold() == "nexusupdater" else "launcher"
        raise SystemExit(main(role, public, UPDATER_VERSION))
