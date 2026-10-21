# Nexus (BOM Manager)

> **New PDM architecture:** Start with [README_PDM.md](README_PDM.md) for the
> CAD Document, Item, association, CAD-to-EBOM build, supplier package, and
> migration workflows.

Nexus **4.1.0** adds [signed releases, managed updates, and workspace compatibility checks](README_RELEASE_UPDATES.md), alongside [shared master work and version-aware PDF/STEP status](docs/master_work_and_deliverables.md).

Build the portable Windows folder with **build_exe.bat**. Start with the [build steps below](#build-a-windows-exe-folder), or open the [complete build guide](README_BUILD_EXE.md) for new-computer setup and troubleshooting.

Users can [save personal BOM column layouts](docs/personal_bom_columns.md) with **Columns → Save for Me**.

Desktop (PyQt5) application for managing a Creo-based BOM with a lightweight, PLM-inspired workflow:

- Multi-project BOM management (parts + assemblies + parent/child relationships)
- Check-out / undo-checkout locking with commit-only check-in
- Commit/validate/merge workflow
- File vault per part (attachments + version history + active version)
- Immutable releases, configurable revision codes, automatic iterations, and exact child bindings
- Baselines (freeze exact released deliverables for reproducible exports)
- Delivery package export (PDF/STEP) with a `manifest.json` (+ optional ZIP)
- BOM-integrated engineering issues, commit resolution claims, validation, risk propagation, and release gates

This repository is intentionally “DB-first”: most features are backed by SQLite tables and surfaced in the UI.

Detailed workflow: [Exact revisions, iterations, assembly bindings, and Creo behavior](docs/exact_revision_iteration_workflow.md)

---

## Build a Windows EXE folder

Use these steps on any compatible **Windows x64 build PC**. Your Python
environment must already contain the PDF, CAD/OCC, Qt and other app libraries.
If it does not, follow [Create a new environment](README_BUILD_EXE.md#create-a-new-environment)
first. The destination PCs running the finished app do not need those libraries
installed.

Open **Anaconda Prompt (CMD)**. Replace the example environment name and project
folder with yours, then run each command in the same window:

```bat
conda activate creo-vcs-py312
cd /d "D:\Projects\Creo-Vcs-primary"
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --check
```

When the check prints **`Build prerequisites OK`**, build:

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --release-public-key "D:\PublicKeys\release_public.key"
```

If only PyInstaller is missing, install it and check again before building:

```bat
build_exe.bat --python "%CONDA_PREFIX%\python.exe" --install-build-tools --check
```

Replace the public-key path with the release administrator's public key. Keep
the private signing key with the publisher; it is not needed on build/client PCs.
The publisher needs the matching private key to sign updates. If only the
existing `tools\licensing\keys\ed25519_public.key` is available, follow the
[release-key setup](README_RELEASE_UPDATES.md#1-prepare-the-release-signing-key)
before building an update-enabled deployment. Update keys do not replace the
separate key used to verify user licenses.
Keep `--python` so the builder uses your chosen environment. Wait for
**`BUILD SUCCESSFUL`**, then distribute the **entire `dist\Nexus` folder**,
including `bootstrap` and both `_internal` folders. It contains `Nexus.exe`,
`CADViewer.exe`, the shared PDF/OCC/Qt runtime, and the launcher/updater. Users
extract that folder and can start the portable `Nexus.exe`; workspace
access and a valid license are still required. For managed installations, users
install `bootstrap\NexusLauncher.exe --install --feed "\\server\NexusReleases"`
from the extracted folder, then use the installed launcher shortcut.

Follow the [release and update guide](README_RELEASE_UPDATES.md) for signing and
publishing, first installation, Updates > Download/Restart, administrator-only
database migrations with backups, [IT installer automation](README_RELEASE_UPDATES.md#installer-automation-for-it),
and compatible rollback. Client startup no
longer applies migrations automatically.

The [complete build guide](README_BUILD_EXE.md) includes prerequisite lists,
fresh environment installation, full Python paths, PowerShell commands,
license placement, logs, runtime checks, and troubleshooting. Python 3.10 was
used for the reference build; other environments, including Python 3.12, must
pass the checks with compatible libraries.

---

## Run from source (Windows)

Run the app:

```powershell
python main3.py
```

Or use the provided launcher:

```powershell
./run.bat
```

Activate a Python environment with the [required app libraries](README_BUILD_EXE.md#required-libraries)
before running from source. Installing PyQt5 alone does not provide PDF preview,
CAD viewing, or the other app dependencies. A valid license and company workspace
are also required for normal startup.

---

## Configuration

`config.py`:

- `DB_NAME`: SQLite database filename (default: `creo_vcs.db` in the repo root)
- `WORKSPACE_ROOT`: reserved for future workspace features

Projects also have a per-project **Working Directory** stored in the DB (`projects.working_directory`).
It is required for file vault operations and exports.

---

## UI overview

Entry point: `main3.py` (PyQt main window `BomGUI`).

Pages (stacked navigation):

- `pages/bom_page.py`: BOM browser/editor + Files tab (attachments, versions, baselines, exports)
- `pages/commit_page.py`: commit queue and validation/merge flow
- `pages/snapshot_page.py`: project snapshots
- `pages/issue_page.py`: engineering Issue Center
- `pages/admin_page.py`: users/roles/permissions and project administration
- `pages/diag_page.py`: diagnostics
- `pages/login_page.py`: authentication

Theme: `modern_theme.qss`.

---

## Core concepts

### 1) BOM (parts + assemblies)

The BOM is stored in `bom`.

Key fields:

- Identity: `aes_number`, `name`, `type` (`prt`, `asm`, etc.)
- CAD naming: `filename`, `drawing`, `base_file_name`, `base_drw_name`
- PLM-lite: `revision` (A..Z..AA..), `lifecycle_state` (WIP/Released/Obsolete), `released_by`, `released_at`
- Project scoping: `project_id`

Parent/child structure is stored in `bom_children` (with `quantity`).

### 2) Locking (check-in / check-out)

Locking is stored in:

- `locks`: current locks (one lock per part)
- `lock_logs`: audit log (check-in/check-out actions)

The UI uses this to prevent parallel edits and to enforce the commit workflow.

### 3) Commits (approval workflow)

Commits represent proposed changes awaiting review:

- Designers create commits (status `Pending`)
- Checkers validate (status `Validated`)
- Merge/approval finalizes (status `Approved`)

The commit record stores metadata like `commit_id`, `title`, `message`, and optional merge fields.

### 4) File Vault (attachments + versions)

Each BOM part can have multiple attachments (PDF/STEP/DWG/OTHER…).

Data model:

- `part_files`: logical attachment (type + display name), points to an active version
- `part_file_versions`: immutable versions (vault path, sha256, size, lifecycle state)

On disk, versions are stored under the project working directory:

```
<working_directory>\vault\part_<part_id>\file_<file_id>\v<version_no>\<original_filename>
```

Release workflow:

- Versions start as `WIP`
- A reviewer can mark a version `Released`
- Exports only include `Released` versions

### 5) Baselines (frozen deliverables)

A baseline captures _exact version IDs_ for PDF/STEP for a selection of parts (optionally including children).
This enables repeatable, audit-friendly exports even if the active version changes later.

### 6) Exports

Two export modes exist:

- **Package export** (`core/services/package_export_service.py`): exports active Released PDF/STEP for selected parts (and optionally children), writes `manifest.json`, can create a ZIP.
- **Baseline export** (`core/services/baseline_service.py`): exports the pinned Released versions from a baseline, writes `manifest.json`.

---

## Permissions / RBAC

RBAC is implemented with:

- `roles`, `permissions`
- `user_roles`, `role_permissions`

Seeded roles include: `admin`, `checker`, `designer` (and `master` is supported).

Seeded permissions include:

- `commit`
- `merge`
- `validate`
- `release_files`
- `set_revision`

UI actions (like releasing a version or setting a part revision) are gated by permissions.

---

## Project duplication

`core/repositories/project_repository.py` provides project duplication:

- Creates a new project
- Links the requesting user to the new project
- Duplicates BOM rows (and remaps IDs)
- Duplicates BOM relations (`bom_children`)
- Duplicates commits for the project (and rewrites identifiers to avoid uniqueness collisions)

Note: file vault attachments (`part_files` / `part_file_versions`) are not duplicated as independent records; if you need “full vault copy per project”, that can be added explicitly.

---

## Database & migrations

SQLite database file: `creo_vcs.db` (default).

Migrations live in `setup/migrations.py` and are tracked via `schema_migrations`.

At startup, `main3.py` attempts to run migrations automatically:

- Migrations run best-effort and won’t block the UI if they fail.
- If something looks “disabled” in the UI (permissions, release buttons, etc.), the first thing to verify is the DB schema is up to date.

Schema (high-level tables):

- Core: `users`, `projects`, `user_projects`
- BOM: `bom`, `bom_children`
- Locking: `locks`, `lock_logs`
- Workflow: `commits`, `signature`
- Vault: `part_files`, `part_file_versions`
- PLM-lite: `roles`, `permissions`, `user_roles`, `role_permissions`
- Baselines: `baselines`, `baseline_files`
- Snapshots: `snapshots`
- Engineering issues: `issues`, `issue_parts`, `issue_comments`, `issue_commit_links`, `issue_attachments`, `issue_history`

Detailed issue architecture and workflows: [`docs/issue_management.md`](docs/issue_management.md).

---

## Repo structure

- `main3.py`: application entry point (Qt main window)
- `pages/`: UI pages and dialogs
- `core/models/`: dataclasses for DB rows
- `core/repositories/`: SQLite access layer
- `core/services/`: business logic (BOM, commits, vault, exports, baselines)
- `setup/`: DB migrations and DB patch scripts
- `assets/`: icons/images
- `modern_theme.qss`: UI theme

---

## Troubleshooting

- **App starts but actions are disabled**: run migrations (start app once, or run `setup/migrations.py` via import) and verify your user has the right roles/permissions.
- **File operations fail with “Project working directory is not set”**: ensure the current project has a valid `working_directory` and you have write access.
- **Exports missing PDF/STEP**: exports intentionally require the active (or baseline-pinned) version to be `Released` and the file must exist on disk.

---

## Notes

This project is evolving toward “PLM-lite”: controlled releases, revision discipline, auditable baselines, and reproducible delivery packages—without the overhead of a full PLM system.


---

## To do

+ fix export package file naming
+ add issues tab and flag in the bom tree
