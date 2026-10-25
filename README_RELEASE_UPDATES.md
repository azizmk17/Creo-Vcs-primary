# Nexus release, installation, and update guide

This guide is for the person publishing Nexus and the users installing it.
Version **4.1.0** introduces a signed company release feed, a local launcher,
a separate updater, and enforced workspace compatibility checks.
The current source version, **4.5.0**, reuses repositories during BOM setup,
defers diagnostics and optional services, and opens the PDF engine only when a
PDF is viewed. Version 4.4.0 added paged EBOM branch queries and indexed
background file checks. Users choose the product and version before loading. See
[Startup and project loading](docs/project_loading.md) and the
[packaged startup measurements](docs/packaged_startup.md).
Version **4.2.0** added graphical **Setup.exe** installation with
shortcuts, so users do not need Command Prompt. It retains the 4.1.1 fix that
releases all related CAD checkouts when a BOM Item is checked in or its checkout
is undone.

## What goes where

| Item | Location / owner |
| --- | --- |
| Application version | `APP_VERSION` in `config.py`; embedded in the EXEs during build. |
| Release signing private key | Kept by the release administrator, outside the release folder and source control. |
| Release public key | Supplied to the builder and compiled into Nexus, Setup, the launcher, and updater. Never trusted from the update server. |
| Published packages | A local/UNC company feed, for example `\\server\NexusReleases`. Users get read access; publishers get write access. |
| Local installation | `%LOCALAPPDATA%\Nexus` by default; another local folder can be chosen in Setup. Contains `bootstrap`, `versions`, `current.json`, and `install.json`. |
| User license | `%APPDATA%\CreoVCS\creovcs.lic`. |
| User settings | Existing workspace settings remain in `%APPDATA%\creovcs`; update preferences are in `%APPDATA%\Nexus`. |
| Company data | The existing workspace containing `creo_vcs.db` and CAD working directories. Updates do not copy or restore it. |

The feed is a filesystem folder, not an HTTP URL. This release does not implement
web downloads, delta packages, or automatic signing-key rotation.

## 1. Prepare the release signing key

Use the release key pair you already hold. The build needs only its **public**
key. A file named `ed25519_public.key` is also accepted when it is the intended
release key; the filename does not determine what it signs.

If no release key pair exists, the administrator can explicitly create one.
In Anaconda Prompt, from the project folder:

```bat
python tools\licensing\update_version.py --keygen "D:\Secure\NexusReleaseKeys"
```

This creates `release_private.key` and `release_public.key` and refuses to
replace existing files. Keep the private key with the publisher. Copy only the
public key to other build PCs.

Release signing and license signing are separate. A release key does not change
the public key used to verify existing licenses. Existing clients' database
announcements must still use their matching **license** signing key.

The project's existing `tools\licensing\keys\ed25519_public.key` contains only
a public key. It lets the builder pin that key; it cannot sign a release or
recover the matching private key. Use it for releases only if the publisher
already holds its matching private key. Otherwise, generate the dedicated
release pair above and build with its `release_public.key`. Introducing a
different release key to installed clients requires a trusted manual deployment;
clients do not accept a new trust key from the feed. License verification keeps
using the separate public key already embedded in Nexus.

For developers running `python main3.py` from source, set
`NEXUS_RELEASE_PUBLIC_KEY` to the 64-character public hex **value**, not a
filename, then select the release folder in Updates. Frozen EXEs ignore this
variable and use their compiled key. Managed installation and activation use
the compiled distribution.

## 2. Set the version and build once

Use numeric `MAJOR.MINOR.PATCH` versions: compatible fixes increment PATCH,
compatible new features increment MINOR, and breaking changes increment MAJOR.
Set `APP_VERSION` in `config.py` before building. Keep a source-control release
tag and the exact tested build. Never replace an already published version with
different files. App versions and database schema numbers are separate.

From **Anaconda Prompt (CMD)**, replace the environment, project, and public-key
paths with your own:

```bat
conda activate creo-vcs-py312
cd /d "D:\Projects\Creo-Vcs-primary"
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --check
```

After the check succeeds:

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --release-public-key "D:\Secure\NexusReleaseKeys\release_public.key" --smoke-opengl
```

`--smoke-opengl` exercises a real 3D viewport and requires an interactive Windows
session with working graphics. You can omit it; PDF, STEP, Qt, startup-import,
and resource checks still run. A publication refuses builds made with
`--no-smoke-test`.

The builder can also find a key through `NEXUS_RELEASE_PUBLIC_KEY_FILE`, or the
local files `tools\licensing\keys\release_public.key` and then
`tools\licensing\keys\ed25519_public.key`. An explicit `--release-public-key`
is recommended to avoid selecting the wrong key. No private key is needed by
the builder or included in the distribution.

Wait for **BUILD SUCCESSFUL**. Output:

```text
dist\Nexus\
    Setup.exe                     Graphical per-user installation
    Nexus.exe
    CADViewer.exe
    _internal\                    Application/Setup PDF/CAD/Qt runtime
    build_manifest.json           Version, build ID, pinned key, schema, test results
    bootstrap\
        NexusLauncher.exe
        NexusUpdater.exe
        _internal\                Smaller independent launcher/updater runtime
```

Distribute this whole folder for the first installation. Users must extract all
files before double-clicking **Setup.exe**; Setup shares the application runtime
in the root `_internal` folder and is not a standalone EXE. It adds no separate installer-tool dependency to the
build environment. Build instructions,
Python environment setup, and troubleshooting are in [README_BUILD_EXE.md](README_BUILD_EXE.md).

## 3. Prepare a shared database when necessary

Clients no longer migrate the company database automatically. Before login,
Nexus checks the schema, minimum app version, and maintenance state using a
read-only connection. Subsequent repository connections recheck that policy
before configuration PRAGMAs or repository data queries. A connection that was
already open is not revoked; close all clients for maintenance.

Inspect the workspace without changing it:

```bat
python tools\migrate_workspace.py --db "\\server\Workspace\creo_vcs.db" --check
```

If it is already compatible, no migration is needed. To apply pending migrations
or deliberately establish the required app version, schedule maintenance and
close **all** Nexus/CADViewer clients, including legacy clients. Then run:

```bat
python tools\migrate_workspace.py --db "\\server\Workspace\creo_vcs.db" --backup-dir "\\server\NexusBackups" --minimum-app-version 4.1.0 --maintenance --confirm-clients-closed
```

The tool checks compatibility, takes a migration lock, sets maintenance mode,
creates and integrity-checks a consistent SQLite backup, runs migrations, checks
the result, and records the version floor. Success clears maintenance mode.
Existing backups are not overwritten. It will not lower the existing version
floor or migrate a schema newer than the tool supports.

If failure occurs after a verified backup was completed, that backup is retained
and maintenance mode stays enabled. If backup creation itself fails, the tool
reports that no verified backup was created and does not start the migration chain.
An administrator must diagnose/recover the workspace before explicitly clearing
`maintenance_mode` in `app_metadata`. The tool never restores the database
silently. Backups include the maintenance flag, so a restored backup also needs
administrator validation before that flag is cleared. Do not clear maintenance
just to suppress an error.

The maintenance flag and process lock cannot stop old clients, external SQLite
tools, or already-open database connections. The closed-client confirmation is an
operational requirement. Do not raise the minimum app version for every routine
patch; raise it when older clients are no longer compatible.

The current build supports schema **49**. Future builds record their actual
supported range in `dist\Nexus\build_manifest.json`; use that range when publishing.

## 4. Publish the tested package

Run this on the publishing PC that holds the release private key. Replace paths
and release notes. The example publishes 4.2.0 as release sequence 1:

```bat
python tools\licensing\update_version.py --package-dir "dist\Nexus" --feed-dir "\\server\NexusReleases" --release-key "D:\Secure\NexusReleaseKeys\release_private.key" --version 4.2.0 --sequence 1 --schema-min 49 --schema-max 49 --notes "Graphical Setup and automatic application shortcuts" --expires-days 90
```

Use a larger sequence on **every** subsequent announcement, including an
explicit rollback. The application version identifies the software; the sequence
identifies publication order. Users remember the highest authenticated sequence.

The publisher checks the package version, pinned public key, schema range,
bootstrap, runtime test results, and file safety. It creates an immutable ZIP,
checks its contents, signs release metadata, and replaces `latest.json` only
after the package is complete. A signature covers the version, build identity,
package path/size/SHA-256, schema range, updater requirement, notes, expiry and
sequence. Expired, modified, wrong-key, or stale announcements are rejected.

```text
\\server\NexusReleases\
    latest.json
    releases\
        Nexus-<version>-<build-id>.zip
```

The signed announcement expires after the selected period. To renew an
announcement for the same immutable package, use `--allow-rollback` with a higher
sequence and the original unchanged build folder; this explicit flag also allows
verified archive reuse. Expiry prevents trusting an indefinitely stale feed;
an expired/unreachable optional feed does not stop a compatible installed app.

### Preserve popup announcements for older versions

The existing command still works and only changes the signed version notice in
the chosen shared database:

```bat
python tools\licensing\update_version.py --version 4.2.0 --private-key "D:\Secure\LicenseKeys\ed25519_private.key" --db "\\server\Workspace\creo_vcs.db"
```

Run it after the new package is available. Use the **license** key matching those
older apps. The publisher also accepts `--db` plus `--private-key` alongside the
new publication options to announce to legacy clients after successful feed
publication. A legacy announcement failure is reported separately; it does not
remove an already published package.

Do not manually edit `latest_version` without its valid signature. Old versions
receive their existing informational popup; they do not acquire an updater
through that popup.

## 5. First installation on a user's PC

No Python, Anaconda, or Command Prompt is needed on the destination PC.

1. Obtain the administrator-approved Nexus ZIP and **extract the entire folder**.
   Do not run Setup from inside the ZIP or copy only its EXE.
2. Open the extracted folder and double-click **Setup.exe**.
3. Keep the default installation folder **`%LOCALAPPDATA%\Nexus`**, or choose a
   different writable local folder. The large PDF/CAD libraries stay in Local
   AppData rather than roaming `%APPDATA%`.
4. If your administrator supplied a company release folder, choose it in Setup,
   for example `\\server\NexusReleases`. It can be left blank for an installation
   without a configured update feed.
5. Optionally select the signed `.lic` file supplied by your administrator.
   Setup copies it to `%APPDATA%\CreoVCS\creovcs.lic` during installation;
   leaving the field blank keeps the existing license. Choose whether to create
   a **Desktop shortcut** before selecting **Install**. A **Start Menu shortcut**
   is created automatically. Setup copies the bootstrap
   and application files, shows progress, and leaves the extracted source intact.
   The installed version includes `Setup.exe`, `Nexus.exe`, and `CADViewer.exe`
   sharing one `_internal` runtime. Signed update packages retain that layout.
6. Choose **Open Nexus** when setup finishes, or start Nexus later through its
   Start Menu/Desktop shortcut.

The shortcut runs the installed launcher at
`%LOCALAPPDATA%\Nexus\bootstrap\NexusLauncher.exe` for the default destination.
For a custom destination, Setup creates shortcuts to that folder's launcher.
Shortcuts always target the stable launcher, which starts the active app version.
Normal per-user installation does not require administrator elevation. Running
Setup from an installed version opens the existing installation options,
including opening Nexus and repairing shortcuts.

Setup uses the release public key compiled into its distribution and checks that
it matches the package. It does not read a replacement trust key from the feed.
If the selected destination already contains a valid installation, Setup keeps
its active version and offers to open Nexus or repair shortcuts. Use the app's
**Updates** window to install a newer release. An incomplete or unrelated
installation is not silently overwritten; select a separate empty destination
or ask the administrator to inspect it.

Setup does not change the company database or CAD workspaces. It keeps the
existing license when the license field is blank and replaces it when a new
license file is selected. If needed, choose the administrator-provided signed
license in Setup; it is saved to `%APPDATA%\CreoVCS\creovcs.lic`. Then select
the existing company workspace when Nexus prompts for it. The launcher checks a previously selected workspace before
starting Nexus; Nexus checks again before constructing login repositories.

**A one-time Setup or IT-managed deployment introduces the launcher and its
trusted public key.** A version predating 4.1 cannot update itself into this
mechanism. Users then receive normal updates through Nexus.

### Installer automation for IT

Run the installation as the Windows user who will run Nexus. The default local
installation and preferences belong to that account; running as SYSTEM or another
administrator installs into that account's profile. The default per-user folder
does not require administrator elevation.

Ordinary users use **Setup.exe** above. The existing command-line installer is
retained for scripted IT deployments. In Command Prompt, from the extracted
trusted distribution:

```bat
start "" /wait "bootstrap\NexusLauncher.exe" --install --feed "\\server\NexusReleases" --quiet --result-json "%TEMP%\Nexus-install-result.json"
echo Installer exit code: %ERRORLEVEL%
type "%TEMP%\Nexus-install-result.json"
```

`start "" /wait` waits for this Windows GUI executable to finish. `--quiet`
suppresses installer dialogs; `--result-json` writes a result to the specified
writable path. Check the process exit code and the JSON `ok` field. Exit code
`0` means the action succeeded; failures return a nonzero code and normally
record an `error` message. The installation command above does not launch Nexus.
It refuses an existing or incomplete installation instead of overwriting it.

The updater also accepts these options for scripted staging or activation:

```bat
start "" /wait "%LOCALAPPDATA%\Nexus\bootstrap\NexusUpdater.exe" --stage --database "\\server\Workspace\creo_vcs.db" --quiet --result-json "%TEMP%\Nexus-stage-result.json"
echo Staging exit code: %ERRORLEVEL%
type "%TEMP%\Nexus-stage-result.json"
```

After staging reports success, close all managed Nexus/CADViewer sessions, then
activate:

```bat
start "" /wait "%LOCALAPPDATA%\Nexus\bootstrap\NexusUpdater.exe" --activate-staged --database "\\server\Workspace\creo_vcs.db" --quiet --result-json "%TEMP%\Nexus-activate-result.json"
echo Activation exit code: %ERRORLEVEL%
type "%TEMP%\Nexus-activate-result.json"
```

Inspect each result before proceeding. Quiet mode does
not skip signature, process, database, or runtime checks; staged runtime tests
still need a Windows session capable of running the GUI diagnostics. Leave
`--launch` off for automation that should finish without opening another session.

## 6. Normal updates

1. Open Nexus through the installed launcher.
2. Nexus checks the configured feed at startup and every 15 minutes. You can
   also select **Updates > Check now**.
3. A verified newer release offers **View update** or **Later**.
4. In Updates, select **Download update**. Nexus keeps working while the full
   package is copied locally, verified, safely extracted, and runtime-tested.
5. When the update is ready, save your work and close CADViewer windows. Select
   **Restart to update** and confirm.
6. The separate updater waits for the original process and all managed Nexus/CAD
   sessions to close. It never kills them. It re-verifies the staged files and
   database compatibility, switches `current.json`, and launches the new version.

The previous application folder is retained. A failed download, signature check,
runtime check, or activation leaves the current pointer unchanged. A later
normal-startup failure requires administrator diagnosis; the updater does not
silently restore the shared database or promise an automatic application rollback.

Portable copies can open Updates and select a release folder, but managed
installation is required to activate an update. Their Download action opens the
graphical Setup when it is present. A managed installation uses the feed
chosen at installation, so ordinary users do not accidentally change its source.

## 7. Administrator rollback

Rollback is explicit and allowed only when the previous application supports the
current database schema and version floor. Keep original approved build folders.
Reannounce the original older package with a **higher sequence** and
`--allow-rollback`; do not edit the archive. For example, if sequence 2 announced
4.1.1, sequence 3 can deliberately reannounce the unchanged 4.1.0 package:

```bat
python tools\licensing\update_version.py --package-dir "D:\ApprovedReleases\4.1.0\Nexus" --feed-dir "\\server\NexusReleases" --release-key "D:\Secure\NexusReleaseKeys\release_private.key" --version 4.1.0 --sequence 3 --schema-min 49 --schema-max 49 --allow-rollback --notes "Administrator-approved compatible rollback"
```

Close the user's app/CAD windows. In Command Prompt on that PC:

```bat
"%LOCALAPPDATA%\Nexus\bootstrap\NexusUpdater.exe" --stage --allow-rollback --database "\\server\Workspace\creo_vcs.db"
"%LOCALAPPDATA%\Nexus\bootstrap\NexusUpdater.exe" --activate-staged --allow-rollback --database "\\server\Workspace\creo_vcs.db" --launch
```

The same archive, signature, sequence, schema, version-floor and process checks
apply. Normal GUI updates cannot silently downgrade. A database migration is not
undone by application rollback. Recovery of a shared database is a separate
coordinated administrator operation.

## Troubleshooting and limits

| Situation | Action |
| --- | --- |
| Build asks for a release public key | Pass `--release-public-key` pointing to the intended public hex key. Never give the builder the private key. |
| Publisher reports key mismatch | Build with the public key belonging to the publisher's release private key. Do not replace clients' trust from an unverified feed. |
| No update appears | Check feed access, signature, expiry, version and sequence. Updates at/below the installed version are not offered as normal upgrades. |
| A release requires a newer updater | Deploy an approved new bootstrap manually. Ordinary updates intentionally do not overwrite the running bootstrap. |
| Setup finds an existing installation | Open the installed app or repair its shortcuts in Setup. Use Updates for a new release; Setup keeps the active version. The IT first-install command refuses an existing managed root. |
| Setup cannot find its libraries | Extract the entire distribution and keep the root `_internal` folder beside Setup. Run Setup from the extracted folder. |
| Activation waits or refuses | Close all Nexus and CADViewer windows for that installation, then retry. No process is forcibly stopped. |
| Workspace requires migration / is too new / is in maintenance | Contact the administrator. Do not bypass the compatibility gate or copy a database from another release. |
| License signature fails | The license must match the separate license public key baked into Nexus. The release signing key cannot fix a license signature. |
| Interrupted staging leaves files | Leave the active version alone. A later staged download uses a separate temporary location; activation requires complete verification. Retained versions/staging consume disk space. |

Full packages are copied; no differential updater is implemented. Disk space must
cover bootstrap, the active version, a staged ZIP/new version, and retained old
versions. Release key rotation and bootstrap updates require a trusted manual
installation. Windows Authenticode signing is separate from this release-feed
signature and is not performed by these tools.
