"""Verify the packaged Setup dialog without installing or opening a workspace."""
import json
import os
from pathlib import Path
import sys
import tempfile


def run_setup_smoke(report_path, public_key_hex):
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)),
              "release_public_key": public_key_hex, "gui": False}
    try:
        if len(bytes.fromhex(public_key_hex)) != 32:
            raise ValueError("The setup must contain a valid release public key")
        from PyQt5.QtWidgets import QApplication
        from core.updates.setup_wizard import SetupDialog
        app = QApplication.instance() or QApplication(["Nexus Setup diagnostic"])
        with tempfile.TemporaryDirectory(prefix="setup-smoke-", dir=destination.parent) as directory:
            target = Path(directory) / "unused-installation"
            dialog = SetupDialog(Path(sys.executable).resolve().parent, public_key_hex, root=target)
            try:
                app.processEvents()
                if dialog.busy or not dialog.install_button.isEnabled():
                    raise RuntimeError("Setup did not present an enabled Install action")
                if not dialog.desktop_checkbox.isChecked():
                    raise RuntimeError("The default Desktop shortcut option is missing")
                if not dialog.license_edit.isEnabled() or not dialog.license_button.isEnabled():
                    raise RuntimeError("Setup did not present the license file option")
                if target.exists():
                    raise RuntimeError("Opening Setup changed the installation directory")

                # Exercise the copy path against temporary AppData, never the
                # account's real license location.
                app_data = Path(directory) / "appdata"
                previous_app_data = os.environ.get("APPDATA")
                try:
                    os.environ["APPDATA"] = str(app_data)
                    license_source = Path(directory) / "smoke.lic"
                    license_source.write_text("setup license copy smoke", encoding="utf-8")
                    from core.updates.setup_wizard import _install_license
                    installed_license = _install_license(license_source)
                    if installed_license != app_data / "CreoVCS" / "creovcs.lic":
                        raise RuntimeError("Setup selected an unexpected license destination")
                    if installed_license.read_text(encoding="utf-8") != license_source.read_text(encoding="utf-8"):
                        raise RuntimeError("Setup did not copy the selected license")
                finally:
                    if previous_app_data is None:
                        os.environ.pop("APPDATA", None)
                    else:
                        os.environ["APPDATA"] = previous_app_data
                result.update(ok=True, gui=True)
            finally:
                dialog.close()
                dialog.deleteLater()
                app.processEvents()
    except Exception as exc:
        result["error"] = str(exc)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
