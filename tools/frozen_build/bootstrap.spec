import os
from pathlib import Path

ROOT = Path(SPEC).resolve().parents[2]
generated = os.environ.get('NEXUS_GENERATED_DIR', str(ROOT))
analysis = Analysis(
    [str(ROOT / 'tools/frozen_build/update_entrypoint.py')],
    pathex=[generated, str(ROOT)], binaries=[], datas=[],
    hiddenimports=['nexus_release_trust', 'PyQt5.QtWidgets', 'PyQt5.sip',
                   'cryptography.hazmat.primitives.asymmetric.ed25519'],
    excludes=['OCC', 'fitz', 'pymupdf', 'numpy', 'pandas', 'scipy', 'matplotlib',
              'IPython', 'pytest', 'PySide2', 'PySide6', 'PyQt6', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
options = dict(exclude_binaries=True, console=False, upx=False, strip=False,
               contents_directory='_internal', icon=str(ROOT / 'assets/pictures/nexus_logo.ico'),
               version=os.environ.get('NEXUS_VERSION_FILE'))
launcher = EXE(pyz, analysis.scripts, [], name='NexusLauncher', **options)
updater = EXE(pyz, analysis.scripts, [], name='NexusUpdater', **options)
release = COLLECT(launcher, updater, analysis.binaries, analysis.datas,
                  upx=False, strip=False, name='bootstrap')
