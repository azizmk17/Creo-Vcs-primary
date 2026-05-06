# cad_viewer.spec
# PyInstaller spec for the standalone CADViewer.exe
#
# !! IMPORTANT — BUILD ENVIRONMENT !!
# This spec MUST be run using the pyoccenv conda Python, NOT the main venv.
# pythonocc is only installed in pyoccenv.
#
# BUILD STEPS
# -----------
#   conda activate pyoccenv
#   cd d:\mineDocs\WORK-SPACE\Python-scripts\creo-vcs\creo_vcs_exe_v2.0-main
#   pip install pyinstaller        # if not already in pyoccenv
#   pyinstaller --clean cad_viewer.spec
#
# OUTPUT
# ------
# dist\CADViewer.exe  ← copy this next to CreoVCS.exe before distributing
#
# The main CreoVCS.exe launcher detects CADViewer.exe at runtime (via
# tools/CAD/step_viewer/launcher.py) and calls it directly — no conda or
# pythonocc installation required on the end user's machine.

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

ROOT = os.path.dirname(os.path.abspath(SPEC))

# ---------------------------------------------------------------------------
# Collect ALL OCC / pythonocc data, binaries, and hidden imports
# ---------------------------------------------------------------------------
occ_datas, occ_binaries, occ_hiddenimports = collect_all('OCC')

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [os.path.join(ROOT, 'tools', 'CAD', 'step_viewer', '__main__.py')],
    pathex=[ROOT],
    binaries=occ_binaries,
    datas=occ_datas,
    hiddenimports=occ_hiddenimports + [
        # Viewer modules
        'tools.CAD.step_viewer.main_window',
        'tools.CAD.step_viewer.face_lifecycle',
        # Step diff engine (used by viewer for face lifecycle DB)
        'tools.CAD.step_diff_engine.step_parser',
        'tools.CAD.step_diff_engine.geometry_fingerprint',
        'tools.CAD.step_diff_engine.diff_engine',
        'tools.CAD.step_diff_engine.database',
        'tools.CAD.step_diff_engine.utils',
        # OCC display subsystem (often not auto-detected)
        'OCC.Display.backend',
        'OCC.Display.qtDisplay',
        # PyQt5
        'PyQt5.sip',
        'PyQt5.QtOpenGL',
        'PyQt5.QtPrintSupport',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'setuptools',
        'pip',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CADViewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # keep visible so OCC errors are readable
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, 'assets', 'pictures', 'creovcs_logo-main.ico'),
)
