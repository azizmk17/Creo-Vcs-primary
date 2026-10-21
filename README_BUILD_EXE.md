# Build Nexus on another Windows computer

Use **`build_exe.bat`** to create a folder containing `Nexus.exe`, `CADViewer.exe`,
and their shared libraries. The build is not tied to a particular username,
Anaconda installation folder, or environment name.

Version 4.1.0 also builds a lightweight `bootstrap` folder containing the managed
launcher and updater. Read [Release, installation, and updates](README_RELEASE_UPDATES.md)
for release-key setup, first installation, signed publishing, and database maintenance.

| Computer | What it needs |
| --- | --- |
| Computer **building** Nexus | Windows x64, the full project source, and a compatible Python environment containing the app libraries and PyInstaller. |
| Computer **running** the finished app | The entire built Nexus folder, a valid license, and access to the company workspace. Python, Anaconda, PDF libraries, and OCC do not need to be installed. |

Start with the steps below if your libraries are already installed. If the PC
has no build environment, follow [Create a new environment](#create-a-new-environment)
first, then return here.

## Build using an existing Anaconda environment

The example environment is **`creo-vcs-py312`**. Replace that name with yours.
The example source folder is **`D:\Projects\Creo-Vcs-primary`**. Replace it with
the folder where you copied or cloned this project.

**Run these commands in Anaconda Prompt (the Command Prompt / CMD version).**
PowerShell uses different syntax; its commands are provided [below](#powershell-alternative).
Run each step in the same prompt window.

### 1. Copy or clone the full project

Copy the complete source project to a writable local folder on the build PC.
Keep `build_exe.bat`, `tools`, `core`, `pages`, `setup`, `assets`, `integrations`,
`main3.py`, `config.py`, and `modern_theme.qss` together. Copying only the BAT file
is not enough. Signing private keys are not required to build the app.

Allow several GB of free space for temporary files and the finished distribution.
Close any app running from this project's `dist\Nexus` folder before rebuilding.
Each build replaces that folder; save a previous release elsewhere if needed.

### 2. Activate your environment and open the project folder

Open **Anaconda Prompt**, then enter:

```bat
conda activate creo-vcs-py312
cd /d "D:\Projects\Creo-Vcs-primary"
```

Confirm that you are in the correct folder and using the intended environment:

```bat
dir build_exe.bat
python -c "import sys; print(sys.executable)"
```

The first command must find `build_exe.bat`. The second should print a path ending
in something like `envs\creo-vcs-py312\python.exe`.

### 3. Check the libraries before building

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --check
```

**Keep `--python` in the commands.** It explicitly selects the environment you
activated. A bare `build_exe.bat` can prefer an existing environment named
`pyoccenv` even when another Conda environment is active.

The check verifies Windows x64, Python imports, the PDF library, the OCC viewer,
OpenCascade resources, and the OpenGL driver DLL. Success ends with:

```text
Build prerequisites OK. No executables were built.
```

If **only PyInstaller is missing**, install it and repeat the check:

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --install-build-tools --check
```

That command needs package index access. It installs PyInstaller 6; it does not
install OCC or the other app libraries. Without `--check`, `--install-build-tools`
continues into a full build after installing the tool.

If another library is missing, fix that environment before continuing. See
[Required libraries](#required-libraries) and [Troubleshooting](#troubleshooting).

### 4. Build the application

Once the check passes, run:

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --release-public-key "D:\PublicKeys\release_public.key"
```

Replace the example public-key path with the release administrator's public hex
key. The private signing key stays with the publisher and is never included.
An explicit file, `NEXUS_RELEASE_PUBLIC_KEY_FILE`, or the local public-key files
described in the release guide supply the trust compiled into all four EXEs.
The existing `tools\licensing\keys\ed25519_public.key` is a public key only:
the publisher must hold its matching private key to sign updates for that build.
If that private key is unavailable, follow [release-key setup](README_RELEASE_UPDATES.md#1-prepare-the-release-signing-key)
and supply the new release public key explicitly. This does not change the key
used to verify user licenses. The library-only `--check` does not validate the
release-key selection; the full build does.

Wait until the prompt reports **`BUILD SUCCESSFUL`**. The build can take several
minutes. During packaging the console may be quiet; progress is saved in
`build\folder\logs\pyinstaller.log`.

The builder uses the selected environment for Python modules, PDF libraries,
OCC extensions, native DLLs, and resources. It does not upgrade your runtime
packages during a normal build.

### 5. Find and distribute the finished folder

The output is inside the project folder:

```text
dist\Nexus\
    Nexus.exe
    CADViewer.exe
    build_manifest.json
    bootstrap\
        NexusLauncher.exe
        NexusUpdater.exe
        _internal\
    _internal\
        Python runtime, Qt plugins, PDF and OCC libraries
        occ-resources\
        assets\pictures\
        modern_theme.qss
```

**Copy the entire `dist\Nexus` folder, including both `_internal` folders and
`bootstrap`. Do not copy only the EXE files.** You can zip the Nexus folder for transfer; extract the whole
archive on the destination PC before starting `Nexus.exe`.

Nexus and CADViewer share one copy of the large native libraries. The 4.0.10
reference build was about **781 MB**; 4.1 also includes the separate bootstrap
runtime. Check the current `build_manifest.json` for the actual size. Do not remove
DLLs to reduce the size. Native binaries are not compressed with UPX.

### 6. Set up the destination PC

On each Windows account that will run Nexus:

1. Press **Win + R**, enter `%APPDATA%`, and press Enter.
2. Create a folder named **CreoVCS** if it does not exist.
3. Copy the signed license into it and name it **creovcs.lic**. The final path is
   `%APPDATA%\CreoVCS\creovcs.lic`.
4. For managed updates, run `bootstrap\NexusLauncher.exe --install --feed "\\server\NexusReleases"`
   from the extracted folder, replacing the feed with your company's release folder.
   Then start `%LOCALAPPDATA%\Nexus\bootstrap\NexusLauncher.exe`. A portable `Nexus.exe`
   can also run directly, but needs managed installation to activate updates.
5. Select the company workspace containing `creo_vcs.db` when prompted.

The license must be signed with the key matching the app's embedded **license**
public key. The release public key selected for updates does not replace it.
Moving the build to another computer does not require generating new signing
keys. Distribute the signed `.lic` file; keep the private signing key with the
license administrator.

The build does not distribute a local database or license. It also does not
include Creo itself: Creo integration still needs the supported Creo installation.
The CAD viewer needs compatible graphics support. Test the finished folder on a
clean Windows x64 PC or VM before distributing it to the company.

Clients check workspace compatibility before login and do not migrate the shared
database automatically. If migration is required, the administrator follows the
backup/maintenance procedure in the release guide.

## Required libraries

The selected build environment must contain all of these:

| Component | Purpose / requirement |
| --- | --- |
| Windows x64 Python | Python 3.10 was used for the reference build. An existing Python 3.12 environment must pass the prerequisite and packaged runtime checks. Its name alone does not establish compatibility. |
| `pythonocc-core` / `OCC` | CAD viewer and STEP comparison, including native OCCT DLLs, Shaders and Units resources. |
| `PyQt5` | App interface, Windows platform plugin, and OpenGL support. |
| `PyMuPDF` | Embedded PDF preview, normally imported as `fitz`. Do not install the unrelated package named `fitz`. |
| `cryptography` | License verification. |
| `openpyxl` | Excel import/export. |
| `PyInstaller` 6 | EXE folder packaging. |

`requirements.txt` alone does **not** supply OCC. It also includes tools used by
the other builders, which this folder build does not need. Install a complete
compatible `pythonocc-core` environment through Conda.

## Create a new environment

Skip this section if your existing environment already passes `--check`.
Install Anaconda or Miniconda for Windows x64, then open **Anaconda Prompt**.
The following setup uses Python 3.10 and OCC 7.9, matching the reference build's
Python/OCC versions. It creates a separate environment named `nexus-build`.

```bat
conda create --name nexus-build --channel conda-forge --strict-channel-priority python=3.10 pythonocc-core=7.9.0 pyqt=5.15 pip
conda activate nexus-build
python -m pip install "PyMuPDF==1.23.26" "cryptography==48.0.0" "openpyxl==3.1.5" "PyInstaller==6.20.0"
```

Environment creation and package installation need access to the relevant Conda
and Python package repositories. On a company network, use your approved mirrors
or ask the environment administrator to provide these packages.

Return to step 2 using `conda activate nexus-build` instead of
`conda activate creo-vcs-py312`. Run `--check` before building. The package versions
above come from the reference build; a new environment still needs to pass the
checks. Each completed build records its own versions in `build_manifest.json`.

The install commands use [Conda environments](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html)
and the [pythonocc-core Conda distribution](https://github.com/tpaviot/pythonocc-core#install-with-conda).

## Use an environment without activating it

Use its full Python path. This also works when Anaconda is installed on another
drive or the environment has a different name. Put `--python` first:

```bat
build_exe.bat --python "E:\Anaconda\envs\my-cad-env\python.exe" --check
build_exe.bat --python "E:\Anaconda\envs\my-cad-env\python.exe" --release-public-key "D:\PublicKeys\release_public.key"
```

Replace the example path with the actual executable. An environment administrator
can also set `NEXUS_BUILD_PYTHON` to that path. Explicit `--python` takes precedence.

## PowerShell alternative

Use this only in a PowerShell session where `conda activate` is available:

```powershell
conda activate creo-vcs-py312
Set-Location "D:\Projects\Creo-Vcs-primary"
.\build_exe.bat --python "$env:CONDA_PREFIX\python.exe" --check
```

After a successful check:

```powershell
.\build_exe.bat --python "$env:CONDA_PREFIX\python.exe" --release-public-key "D:\PublicKeys\release_public.key"
```

PowerShell uses `$env:CONDA_PREFIX` and `Set-Location`. Anaconda Prompt / CMD uses
`%CONDA_PREFIX%` and `cd /d`. Do not mix the two sets of commands.

## Checks, logs, and optional commands

By default, **both built executables** are tested after packaging. Diagnostics
render a generated PDF, write a STEP box and read it with the app's parser,
render a Qt widget, import the CAD viewer and app pages, and check UI/Creo
resources. Developer Python, Conda, Qt and OCCT environment paths are removed
during the checks. SQLite connections are blocked during app import checks;
these diagnostics do not require a license or access your company database.

| File | What it tells you |
| --- | --- |
| `build\folder\logs\pyinstaller.log` | Packaging progress and errors. |
| `build\folder\logs\bootstrap-build.log` | Launcher/updater packaging progress and errors. |
| `build\folder\logs\NexusLauncher-self-test.json` and `NexusUpdater-self-test.json` | Bootstrap verification-key self-test results. |
| `build\folder\logs\Nexus-smoke.json` | Nexus runtime check results. |
| `build\folder\logs\CADViewer-smoke.json` | CAD viewer runtime check results. |
| `build\folder\logs\Nexus-smoke.log` and `CADViewer-smoke.log` | Diagnostic process output. |
| `dist\Nexus\build_manifest.json` | App version, environment package versions, size, and runtime check results. |

To also test a real 3D viewport on an interactive build PC:

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --release-public-key "D:\PublicKeys\release_public.key" --smoke-opengl
```

To clear this builder's intermediate files and rebuild:

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --release-public-key "D:\PublicKeys\release_public.key" --clean
```

`--clean` clears `build\folder`, including its old logs, then builds again.
Other output directories, source files, workspace databases and licenses are
left alone. Every full build replaces `dist\Nexus`.

For a build session that cannot run GUI diagnostics, `--no-smoke-test` skips
application runtime checks and leaves their manifest report empty; bootstrap
self-tests still run. It does not fix missing libraries. Such a build cannot be
published to the signed release feed. Rebuild with the required checks on a
Windows desktop before publishing.

## Troubleshooting

| Message / symptom | What to do |
| --- | --- |
| `conda` is not recognized | Open Anaconda Prompt from the Windows Start menu. If it is absent, install Anaconda/Miniconda or ask the environment administrator. |
| Environment `creo-vcs-py312` not found | Run `conda env list`, then activate the actual environment name. |
| `build_exe.bat` is not recognized | Use `cd /d` to enter the full project folder. `dir build_exe.bat` must find the file. In PowerShell, use `.\build_exe.bat`. |
| The wrong Python/environment appears | Pass `--python "%CONDA_PREFIX%\python.exe"` after activating your environment, or supply its full Python path. |
| `Python executable not found` | Check the quoted path. `%CONDA_PREFIX%` is CMD syntax and requires an activated Conda environment. |
| PyInstaller is missing | Run the step 3 command with `--install-build-tools --check`, then build once the check passes. |
| `OCC`, OCCT resources, or `TKOpenGl.dll` is missing | Select the environment containing the complete pythonocc-core installation. Installing PyInstaller or copying a single DLL does not supply the CAD runtime. See the new-environment setup above. |
| PyMuPDF / PDF preview is unavailable | Install PyMuPDF in the selected environment. It is different from the package named `fitz`. |
| Only PySide or PyQt6 is installed | This app requires PyQt5 in the build environment. |
| Access denied when replacing `dist\Nexus` | Close Nexus/CADViewer running from that folder and check that your project folder is writable. |
| The console is quiet | Open `build\folder\logs\pyinstaller.log`. Wait for `BUILD SUCCESSFUL`; do not assume the build has finished because EXEs appeared. |
| A packaged runtime check fails | Read the matching smoke JSON and log, correct the reported dependency/resource problem, and rebuild. An EXE existing is not proof of a complete working build. |
| `--smoke-opengl` fails in a remote/headless session | Retry on an interactive Windows desktop with working graphics support. Default checks do not exercise a real 3D viewport. |
| The app cannot find DLLs on the destination PC | Extract/copy the entire Nexus folder, including `_internal`, and launch from the extracted folder. |
| The app reports a license signature error | Obtain a license signed with the matching key. Renaming or copying an incompatible license does not change its signature. |

## Which build file to use

Use **`build_exe.bat`** for the shared EXE-and-DLL folder described here.
`build.bat` is the older PyArmor/one-file pipeline. `build_nuitka.ps1` is the
alternative Nuitka builder. This folder build does not invoke PyArmor.

The folder layout follows the [PyInstaller spec-file documentation](https://pyinstaller.org/en/stable/spec-files.html).
