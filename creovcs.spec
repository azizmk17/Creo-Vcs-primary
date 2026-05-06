# creovcs.spec
# PyInstaller spec for CreoVCS v2.0
#
# BUILD WORKFLOW
# --------------
# Do NOT run pyinstaller directly on main3.py.
# Always run build.bat which:
#   1. Obfuscates sources into dist_obf/ via PyArmor
#   2. Calls pyinstaller --clean creovcs.spec
#
# The spec reads from dist_obf/ (the obfuscated copy), not the source tree.

import os
from glob import glob
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# ---------------------------------------------------------------------------
# Resolve paths relative to this spec file
# ---------------------------------------------------------------------------
ROOT      = os.path.dirname(os.path.abspath(SPEC))
OBFDIR    = os.path.join(ROOT, 'dist_obf')
ASSETS    = os.path.join(ROOT, 'assets', 'pictures')
QSS       = os.path.join(ROOT, 'modern_theme.qss')
ICON      = os.path.join(ROOT, 'assets', 'pictures', 'creovcs_logo-main.ico')

# ---------------------------------------------------------------------------
# PyArmor runtime — glob because the folder name contains a random suffix
# ---------------------------------------------------------------------------
pyarmor_runtime_dirs = glob(os.path.join(OBFDIR, 'pyarmor_runtime_*'))

# ---------------------------------------------------------------------------
# Data files to embed in the EXE
# ---------------------------------------------------------------------------
datas = [
    (ASSETS, 'assets/pictures'),
]

if os.path.isfile(QSS):
    datas.append((QSS, '.'))

for rt_dir in pyarmor_runtime_dirs:
    datas.append((rt_dir, os.path.basename(rt_dir)))

# ---------------------------------------------------------------------------
# Hidden imports
# PyInstaller cannot detect modules loaded via dynamic imports, string-based
# imports, or plugin-style loading.  List them all explicitly.
# ---------------------------------------------------------------------------
hidden = [
    # ── Config & workspace ────────────────────────────────────────────────
    'config',
    'utils',
    'core.workspace_config',
    'core.session_manager',

    # ── Licensing (every sub-module, dynamic imports inside manager) ──────
    'core.licensing',
    'core.licensing.manager',
    'core.licensing.loader',
    'core.licensing.crypto',
    'core.licensing.machine',
    'core.licensing.payload',
    'core.licensing.checks',
    'core.licensing.exceptions',
    'core.licensing.revocation',
    'core.licensing.version_check',

    # ── Models ────────────────────────────────────────────────────────────
    'core.models.baseline_file_model',
    'core.models.baseline_model',
    'core.models.bom_children_model',
    'core.models.bom_model',
    'core.models.commit_model',
    'core.models.lock_logs_model',
    'core.models.lock_model',
    'core.models.merge_model',
    'core.models.part_file_model',
    'core.models.part_file_version_model',
    'core.models.signature_model',
    'core.models.user_model',

    # ── Repositories ──────────────────────────────────────────────────────
    'core.repositories.audit_repository',
    'core.repositories.baseline_file_repository',
    'core.repositories.baseline_repository',
    'core.repositories.bom_children_repository',
    'core.repositories.bom_repository',
    'core.repositories.commit_repository',
    'core.repositories.diag_repository',
    'core.repositories.lock_repository',
    'core.repositories.merge_repository',
    'core.repositories.part_doc_ack_repository',
    'core.repositories.part_file_repository',
    'core.repositories.permission_repository',
    'core.repositories.project_repository',
    'core.repositories.role_permission_repository',
    'core.repositories.role_repository',
    'core.repositories.signature_repository',
    'core.repositories.snapshot_repository',
    'core.repositories.user_repository',
    'core.repositories.user_role_repository',

    # ── Services ──────────────────────────────────────────────────────────
    'core.services.admin_service',
    'core.services.audit_service',
    'core.services.base_service',
    'core.services.baseline_service',
    'core.services.bom_service',
    'core.services.commit_service',
    'core.services.diag_service',
    'core.services.merge_service',
    'core.services.package_export_service',
    'core.services.part_doc_ack_service',
    'core.services.part_file_service',
    'core.services.permission_decorators',
    'core.services.permission_service',
    'core.services.project_service',
    'core.services.role_service',
    'core.services.snapshot_service',
    'core.services.ui_permission',
    'core.services.user_service',
    'core.services.workers.snapshot_worker',

    # ── Pages ─────────────────────────────────────────────────────────────
    'pages.admin_page',
    'pages.bom_page',
    'pages.commit_page',
    'pages.diag_page',
    'pages.login_page',
    'pages.part_dialog',
    'pages.pdf_viewer_widget',
    'pages.snapshot_page',
    'pages.dialogs.addChild_dialog',
    'pages.dialogs.merge_dialog',
    'pages.dialogs.package_parts_dialog',
    'pages.dialogs.progress_dialog',
    'pages.dialogs.snapshot_detail_dialog',

    # ── Setup ─────────────────────────────────────────────────────────────
    'setup.migrations',

    # ── Cryptography (lazy-imported inside crypto.py) ─────────────────────
    'cryptography',
    'cryptography.hazmat',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.asymmetric.ed25519',
    'cryptography.hazmat.backends',
    'cryptography.exceptions',

    # ── Windows-only stdlib (used by machine.py for machine-ID binding) ───
    'winreg',

    # ── CAD viewer launcher (pure stdlib, spawns subprocess into conda env) ─
    'tools.CAD.step_viewer.launcher',

    # ── Step diff engine (pure Python, no pythonocc at module level) ─────
    'tools.CAD.step_diff_engine.geometry_fingerprint',
    'tools.CAD.step_diff_engine.step_parser',
    'tools.CAD.step_diff_engine.diff_engine',
    'tools.CAD.step_diff_engine.database',
    'tools.CAD.step_diff_engine.utils',

    # ── PyQt5 internals that may not be auto-detected ─────────────────────
    'PyQt5.sip',
    'PyQt5.QtPrintSupport',   # needed by some print/export flows
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [os.path.join(OBFDIR, 'main3.py')],
    pathex=[OBFDIR, ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy CAD rendering libs — launched as subprocess in conda env, not bundled
        # NOTE: tools.CAD.step_viewer.launcher and step_diff_engine are in hiddenimports above
        'pythonocc',
        'OCC',
        # Dev-only
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
    name='CreoVCS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,          # compress with UPX if available
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,     # windowed – no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version_file=None,
)
