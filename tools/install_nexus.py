"""Install an explicitly trusted Nexus distribution without modifying user data."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from core.updates.desktop import main as installer_main
    from core.updates.trust import release_public_key
    from config import APP_VERSION
    # Source CLI performs the same manual installation as the frozen launcher.
    if "--install" not in sys.argv[1:]:
        sys.argv.insert(1, "--install")
    return installer_main("launcher", release_public_key(), APP_VERSION)


if __name__ == "__main__":
    raise SystemExit(main())
