# Nexus Nuitka Builder Guide

This document explains how the team should build the Nexus desktop application using `build_nuitka.ps1`.

The builder creates a standalone Windows distribution with Nuitka. It packages the main Nexus app, Qt assets, required DLLs, and optionally the CAD Viewer/OpenCascade runtime.

## Builder file

Use:

```powershell
.\build_nuitka.ps1
```

Main output:

```text
dist_nuitka\Nexus-release\Nexus.exe
```

Build logs:

```text
dist_nuitka\logs
```

Build manifest:

```text
dist_nuitka\Nexus-release\build_manifest.json
```

## Recommended production build

Run this from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean
```

This is the normal command for a production package.

It will:

- clean the previous Nuitka output;
- install or upgrade the Nuitka build toolchain;
- build `main3.py` as `Nexus.exe`;
- build the CAD Viewer as `CADViewer.exe`;
- copy needed runtime DLLs;
- produce a clean release folder;
- remove unsafe local development files from the release folder.

## Fast development build

Use this when checking if the build pipeline still works and you do not need the most optimized binary:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -BuildProfile Fast
```

`Fast` disables link-time optimization. The generated app may be less optimized, but the build is usually quicker.

## Build without CAD Viewer

Use this if the build machine does not have the OpenCascade/OCC Python environment, or when you only need the main Nexus app:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -SkipCadViewer
```

This skips:

- `tools\CAD\step_viewer\__main__.py`
- `CADViewer.exe`
- OCC-specific DLL collection

## Use a specific Python environment

The builder auto-detects Python, preferring common `pyoccenv` locations. If detection picks the wrong Python, pass the environment explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Python "C:\Users\mkazi\miniconda3\envs\pyoccenv\python.exe" -Clean
```

For a full build with CAD Viewer, the Python environment must contain:

- `PyQt5`
- `nuitka`
- `ordered-set`
- `zstandard`
- `OCC`

For a main-app-only build, `OCC` is not required if `-SkipCadViewer` is used.

## Skip pip installation

If the build environment is locked and dependencies are already installed, use:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -SkipPipInstall
```

This prevents the script from running:

```powershell
python -m pip install --upgrade nuitka ordered-set zstandard wheel
```

## Optional smoke test

To check that the generated `Nexus.exe` starts without immediately crashing:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -SmokeTest
```

The smoke test starts the app hidden for 5 seconds. If it stays alive, the script stops it and reports success.

Use this carefully on machines where app startup may require manual project selection, licenses, network paths, or user interaction.

## Verbose Nuitka command output

To print the full Nuitka command line:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -VerboseNuitka
```

Use this when debugging build flags.

## Build profiles

| Profile | Command value | Purpose |
|---|---:|---|
| Release | `-BuildProfile Release` | Production build. Uses link-time optimization. Slower build, better final runtime. |
| Fast | `-BuildProfile Fast` | Development build. Faster compile, less optimized final app. |

Default profile:

```text
Release
```

## What the builder includes

The main app build includes:

- `main3.py`
- `core`
- `pages`
- `setup`
- `utils.py`
- `config.py`
- `openpyxl`
- `modern_theme.qss`
- `assets\pictures`
- the CAD Viewer launcher module
- STEP diff engine package

The CAD Viewer build includes:

- `tools\CAD\step_viewer`
- `tools\CAD\step_diff_engine`
- `OCC`

## Heavy libraries intentionally excluded

The builder tells Nuitka not to follow large libraries that are not part of normal Nexus runtime:

- `pandas`
- `scipy`
- `sklearn`
- `matplotlib`
- `jupyter`
- `notebook`
- `IPython`
- `pytest`
- `tkinter`
- Python test packages

This keeps the generated application smaller and avoids spending build time on unnecessary modules.

If a future Nexus feature truly needs one of these libraries at runtime, remove the matching `--nofollow-import-to=...` line from `build_nuitka.ps1`.

## DLL handling

The builder copies important native DLLs from the Python environment into the release folder.

Runtime DLLs include examples like:

- `vcruntime*.dll`
- `msvcp*.dll`
- `concrt*.dll`
- `libcrypto*.dll`
- `libssl*.dll`
- `sqlite3.dll`
- `zlib*.dll`
- `libffi*.dll`

When CAD Viewer is enabled, it also collects OCC/OpenCascade-related DLLs:

- `TK*.dll`
- `TKernel.dll`
- `freetype*.dll`
- `freeimage*.dll`
- `tbb*.dll`
- `jemalloc*.dll`
- `gl2ps*.dll`

This is important because Nuitka can compile the Python code correctly while still missing native DLLs needed by Qt, OpenSSL, SQLite, or OpenCascade.

## Files that must not be shipped

The builder removes these local development files from the release folder if they appear:

```text
creo_vcs.db
creovcs.db
creovcs.lic
trail.txt.1
```

Production data, vault data, project databases, and license files must stay in their configured runtime locations, not inside the shipped executable folder.

## Expected successful result

At the end of a successful build, the script prints:

```text
BUILD SUCCESSFUL
Executable : ...\dist_nuitka\Nexus-release\Nexus.exe
Release dir: ...\dist_nuitka\Nexus-release
Size       : ...
Files      : ...
Logs       : ...\dist_nuitka\logs
```

The folder to package or copy to another machine is:

```text
dist_nuitka\Nexus-release
```

Do not copy only `Nexus.exe`. The `.exe` depends on the files and DLLs beside it.

## Common problems

### Python was not found

Use:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Python "C:\path\to\python.exe" -Clean
```

### OCC module is missing

This happens when building the CAD Viewer with a Python environment that does not contain OpenCascade.

Use the correct environment:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Python "C:\Users\mkazi\miniconda3\envs\pyoccenv\python.exe" -Clean
```

Or skip CAD Viewer:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -SkipCadViewer
```

### Visual Studio / MSVC problem

Nuitka needs a working C/C++ compiler on Windows. The script uses:

```text
--msvc=latest
```

Install Microsoft Visual Studio Build Tools with the C++ workload if Nuitka reports a compiler problem.

### Build is too slow

Use the fast profile:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -BuildProfile Fast
```

Or skip CAD Viewer while testing:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -SkipCadViewer
```

### App starts but CAD Viewer does not

Check:

- `CADViewer.exe` exists in `dist_nuitka\Nexus-release`;
- OCC DLLs exist in the same release folder;
- the build used the `pyoccenv` Python environment;
- the build was not run with `-SkipCadViewer`.

### Missing Qt platform plugin

If the app fails with a Qt platform plugin error, check the Nuitka logs in:

```text
dist_nuitka\logs
```

The script uses:

```text
--enable-plugin=pyqt5
--include-qt-plugins=sensible
```

If the issue persists on a specific machine, rebuild on the target Python/Qt environment.

## Team build checklist

Before releasing a build:

1. Pull the latest source.
2. Close any running Nexus executable from the previous build.
3. Run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean
   ```

4. Confirm `BUILD SUCCESSFUL`.
5. Start:

   ```text
   dist_nuitka\Nexus-release\Nexus.exe
   ```

6. Open a real project.
7. Check the main pages:
   - Structure
   - Dashboard
   - Commit
   - Diagnostics
   - CAD Viewer if included
8. Package the full folder:

   ```text
   dist_nuitka\Nexus-release
   ```

## Maintenance notes

When adding a new runtime package to Nexus:

1. Check whether Nuitka detects it automatically.
2. If not, add an `--include-package=...` or `--include-module=...` line in `build_nuitka.ps1`.
3. If the package has native DLLs, extend `Copy-NativeDlls`.
4. If the package is large and not needed at runtime, add a `--nofollow-import-to=...` exclusion.

Keep the builder strict. A clean build should fail early if a required runtime module is missing, instead of producing a broken release folder.
