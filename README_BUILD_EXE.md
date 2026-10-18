# Windows folder build

Run **build_exe.bat** from this checkout. It produces:

```text
dist\Nexus\
    Nexus.exe
    CADViewer.exe
    build_manifest.json
    _internal\
        Python runtime, Qt plugins, PDF and OpenCascade libraries
        occ-resources\
        assets\pictures\
        modern_theme.qss
```

Distribute the **entire `dist\Nexus` folder**, optionally zipped. Start `Nexus.exe`.
The destination PC does not need Python, Conda, PyMuPDF, or pythonocc-core.
Use a compatible Windows x64 PC with graphics support for the CAD viewer.
Existing workspace selection and licensing still apply. Creo integration still
requires a supported Creo installation; bundling OCC does not bundle Creo.

## Build environment

Use a Windows x64 Python environment with the application's runtime dependencies
already installed: PyQt5, PyMuPDF, cryptography, openpyxl, and **pythonocc-core**.
The BAT prefers `%USERPROFILE%\miniconda3\envs\pyoccenv\python.exe`, then the
corresponding Anaconda environment, an active Conda environment, and PATH Python.
Override it with:

```bat
build_exe.bat --python "C:\path\to\pyoccenv\python.exe"
```

You can also set `NEXUS_BUILD_PYTHON` to that executable. All build commands use
the selected interpreter; activating Conda is not required. The build does not
upgrade your runtime packages. `pythonocc-core` must be installed through a
compatible Conda environment; it is not included in `requirements.txt`.

Check prerequisites without building:

```bat
build_exe.bat --check
```

Install PyInstaller 6 if needed (requires package index access):

```bat
build_exe.bat --install-build-tools
```

## Build and checks

```bat
build_exe.bat
```

The builder includes native OCC extensions, PDF binaries, Qt plugins, OCCT
resources, and transitive DLL dependencies. Nexus needs OCC for STEP comparison
inside the main process, as well as for the separate CAD viewer. Both
executables share `_internal`, so large DLLs are stored once. The collector
follows required native imports instead of copying the entire Conda environment.
The complete CAD runtime can still make the folder several hundred MB or more.
Native binaries are not compressed with UPX.

After packaging, both executables run a diagnostic that renders a generated PDF,
writes a STEP box and reads it with the application's STEP parser, renders a Qt
widget, and imports the CAD viewer. These checks run from a temporary working
directory with developer Python, Conda, Qt and OCCT environment paths removed.
Startup/page imports and bundled UI/Creo integration resources are checked too,
with SQLite connections blocked while importing application modules.
They do not open a company database or require a license. Normal app startup
still checks the workspace and license.

To also test a real OpenGL CAD viewport on the build PC:

```bat
build_exe.bat --smoke-opengl
```

`--no-smoke-test` skips diagnostics when the build session cannot run GUI code;
the manifest then contains an empty smoke test report. Test the full folder on a
clean Windows PC or VM before distributing it to the company.

Logs and diagnostic JSON reports are written to `build\folder\logs`. The release
manifest records the app version, package versions, size, and diagnostic results.
The version is read from `config.py` and embedded in both EXEs' Windows metadata.

The builder replaces only `dist\Nexus`. To discard its intermediate build cache:

```bat
build_exe.bat --clean
```

Other output folders, source files, workspace databases, and licenses are left
alone. Only explicit application resources are included; local databases,
licenses, debug logs and trails are not distributed.

## Other builders

`build_exe.bat` is the shared PyInstaller folder build. The older `build.bat`
remains the separate PyArmor/one-file pipeline; `build_nuitka.ps1` remains the
alternative Nuitka builder. This folder build does not invoke PyArmor.

The folder layout follows the [PyInstaller spec-file documentation](https://pyinstaller.org/en/stable/spec-files.html).
