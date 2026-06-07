# CreoVCS (BOM Manager)

Desktop (PyQt5) application for managing a Creo-based BOM with a lightweight, PLM-inspired workflow:

- Multi-project BOM management (parts + assemblies + parent/child relationships)
- Check-in / check-out locking
- Commit/validate/merge workflow
- File vault per part (attachments + version history + active version)
- Release controls + alphabetic revisions
- Baselines (freeze exact released deliverables for reproducible exports)
- Delivery package export (PDF/STEP) with a `manifest.json` (+ optional ZIP)
- BOM-integrated engineering issues, commit resolution claims, validation, risk propagation, and release gates

This repository is intentionally “DB-first”: most features are backed by SQLite tables and surfaced in the UI.

---

## Quick start (Windows)

Run the app:

```powershell
python main3.py
```

Or use the provided launcher:

```powershell
./run.bat
```

### Dependencies

- Python 3.x
- `PyQt5`

If you don’t already have PyQt5 installed:

```powershell
python -m pip install PyQt5
```

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
