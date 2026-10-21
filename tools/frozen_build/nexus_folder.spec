# Built by build_exe.bat. Setup and both apps share one Analysis and native runtime.
import os
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPEC).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.frozen_build.dependencies import collect_runtime_dependencies

datas, binaries, hidden = collect_runtime_dependencies()
datas += [
    (str(ROOT / 'assets' / 'pictures'), 'assets/pictures'),
    (str(ROOT / 'modern_theme.qss'), '.'),
    (str(ROOT / 'integrations/creo_jlink/async/NexusStructureScan.java'), 'integrations/creo_jlink/async'),
    (str(ROOT / 'integrations/creo_jlink/src/MiniJson.java'), 'integrations/creo_jlink/src'),
]
hidden += ['main3', 'config', 'utils', 'nexus_release_trust', 'tools.frozen_build.smoke',
           'tools.frozen_build.setup_smoke',
           'tools.CAD.step_viewer.main_window', 'tools.CAD.step_viewer.face_lifecycle',
           'tools.CAD.step_viewer.launcher', 'PyQt5.QtOpenGL', 'PyQt5.QtPrintSupport',
           'PyQt5.sip', 'openpyxl', 'cryptography.hazmat.primitives.asymmetric.ed25519']
for package in ('core', 'pages', 'setup', 'tools.CAD.step_diff_engine'):
    hidden += collect_submodules(package, filter=lambda name: not name.endswith('.step_diff_gui'))

analysis = Analysis(
    [str(ROOT / 'tools/frozen_build/entrypoint.py')], pathex=[os.environ.get('NEXUS_GENERATED_DIR', str(ROOT)), str(ROOT)],
    binaries=binaries, datas=datas, hiddenimports=sorted(set(hidden)),
    runtime_hooks=[str(ROOT / 'tools/frozen_build/runtime_hook.py')],
    excludes=['pytest', 'IPython', 'notebook', 'jupyter', 'matplotlib', 'scipy', 'pandas',
              'PySide2', 'PySide6', 'PyQt6', 'wx', 'tkinter'],
    # This worker hashes __file__; load a real source file rather than a PYZ path.
    module_collection_mode={'core.integrations.creo_structure_worker': 'py'},
    noarchive=False,
)
pyz = PYZ(analysis.pure)
options = dict(exclude_binaries=True, console=False, debug=False, strip=False, upx=False,
               contents_directory='_internal', icon=str(ROOT / 'assets/pictures/nexus_logo.ico'),
               version=os.environ.get('NEXUS_VERSION_FILE'))
nexus = EXE(pyz, analysis.scripts, [], name='Nexus', **options)
viewer = EXE(pyz, analysis.scripts, [], name='CADViewer', **options)
setup = EXE(pyz, analysis.scripts, [], name='Setup', **options)
release = COLLECT(nexus, viewer, setup, analysis.binaries, analysis.datas,
                  strip=False, upx=False, name='Nexus')
