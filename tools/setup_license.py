"""
setup_license.py
=================
CreoVCS License Installer.

Run this once after receiving your ``creovcs.lic`` file from your
administrator.  It copies the license to the correct system location so
CreoVCS can always find it, regardless of where the application is installed.

Usage (command line):
    python setup_license.py                     # opens a file picker
    python setup_license.py path/to/my.lic      # installs directly

The license is installed to:
    %APPDATA%\\CreoVCS\\creovcs.lic
    (e.g. C:\\Users\\YourName\\AppData\\Roaming\\CreoVCS\\creovcs.lic)
"""

import os
import shutil
import sys
from pathlib import Path

# ---- Destination ---------------------------------------------------------
_APP_DATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
LICENSE_DIR  = _APP_DATA / "CreoVCS"
LICENSE_DEST = LICENSE_DIR / "creovcs.lic"


def install_license(src: Path) -> None:
    """Copy *src* to the standard license location."""
    if not src.exists():
        raise FileNotFoundError(f"License file not found: '{src}'")
    if src.suffix.lower() != ".lic":
        raise ValueError(f"Expected a .lic file, got: '{src.name}'")

    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, LICENSE_DEST)


def _run_gui(preselected: Path | None = None) -> None:
    """Show a simple Tkinter installer window."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("CreoVCS License Setup")
    root.resizable(False, False)
    root.geometry("480x200")

    # Centre the window on the screen.
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - 480) // 2
    y = (sh - 200) // 2
    root.geometry(f"480x200+{x}+{y}")

    lic_var = tk.StringVar(value=str(preselected) if preselected else "")

    tk.Label(root, text="CreoVCS License Installer", font=("Segoe UI", 13, "bold"),
             pady=12).pack()

    frame = tk.Frame(root)
    frame.pack(fill="x", padx=20)

    entry = tk.Entry(frame, textvariable=lic_var, width=42, font=("Segoe UI", 9))
    entry.pack(side="left", padx=(0, 6))

    def browse():
        path = filedialog.askopenfilename(
            title="Select CreoVCS License File",
            filetypes=[("License files", "*.lic"), ("All files", "*.*")],
        )
        if path:
            lic_var.set(path)

    tk.Button(frame, text="Browse…", command=browse, width=10).pack(side="left")

    status_var = tk.StringVar()
    tk.Label(root, textvariable=status_var, font=("Segoe UI", 9),
             fg="gray", wraplength=440).pack(pady=6)

    def do_install():
        src = Path(lic_var.get().strip())
        try:
            install_license(src)
            status_var.set(f"Installed to:\n{LICENSE_DEST}")
            messagebox.showinfo(
                "License Installed",
                f"Your CreoVCS license has been installed successfully.\n\n"
                f"Location:\n{LICENSE_DEST}\n\n"
                "You can now start CreoVCS.",
            )
            root.destroy()
        except (FileNotFoundError, ValueError, OSError) as exc:
            status_var.set("")
            messagebox.showerror("Installation Failed", str(exc))

    tk.Button(root, text="Install License", command=do_install,
              bg="#0078d4", fg="white", font=("Segoe UI", 10, "bold"),
              padx=16, pady=6, relief="flat", cursor="hand2").pack(pady=4)

    root.mainloop()


def _run_cli(src: Path) -> None:
    """Install from the command line and print a result."""
    try:
        install_license(src)
        print(f"License installed successfully.")
        print(f"  Source : {src}")
        print(f"  Target : {LICENSE_DEST}")
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        # CLI mode — path passed as argument.
        _run_cli(Path(sys.argv[1]))
    else:
        # GUI mode — no argument given.
        preselected = None
        # If a .lic file sits next to this script, pre-fill the picker.
        for candidate in Path(__file__).parent.glob("*.lic"):
            preselected = candidate
            break
        _run_gui(preselected)
