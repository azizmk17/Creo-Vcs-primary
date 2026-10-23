# Startup and project loading — Nexus 4.3.0

## Choose the project and version together

1. Sign in. On the first login, Nexus opens without loading a default project.
2. Select the **PRODUCT** in the top toolbar.
3. Select the desired **VERSION**.
4. Click **Load Project**.

Selecting either dropdown only prepares the selection. It does not change the
active project, scan its files, or load its first version. The header and current
project label continue to describe the active project until Load Project is
clicked. The button reads **Loaded** when the selectors match the active version.

On later logins, a valid, explicitly saved last project/version is restored.
Nexus no longer chooses the project's `is_current` version as a first-login
fallback. If the saved project is unavailable, choose an accessible project and
version in the toolbar.

## What happens during startup

Workspace compatibility and sign-in remain required before opening the shell.
There is no artificial four-second startup delay, and opening the shell no longer
waits for the first BOM result.

After the BOM panel becomes visible, its loader shows **Loading project
structure…** while a worker fetches the structure, CAD associations, folders,
variants, and health summary. Qt rows are created on the UI thread when the
results arrive. The CAD Structure view uses the same background query mechanism.

Directory diagnostics run separately and display **Checking project files in
the background…** below the BOM. The structure can be used before the file check
finishes. Existing diagnostic findings and deliverable status rules still apply;
moving the checks does not reset saved PDF/STEP status.

Commit Workspace, Issue Center, and Manager Dashboard are constructed when first
opened. They do not start project queries during shell startup. CAD approval
cleanup is also deferred until the shell is visible.

Switching projects cancels obsolete requests and ignores their results. Each
worker keeps the project it started with, including diagnostics that synchronize
file metadata. A failed structure query stops the loader and provides a
**Refresh** retry message. Closing waits for cancelled workers to finish safely;
an in-progress filesystem or database call may need to return first.

## Large-project performance: discussion proposal only

The changes above improve when work starts and keep database queries off the UI
thread. They do not guarantee that a huge project's complete load or directory
check finishes quickly. The following optimizations have **not** been implemented.

Start by measuring shell construction, structure queries, Qt row creation,
directory enumeration, and per-file checks separately on a representative copy
of the company project. Record first usable BOM time separately from the time
needed to finish all checks. Compare local storage with the company share before
choosing the next change.

The current code reveals two concrete candidates:

- `DiagService.sync_bom_files` lists files once, but `_sync_bom_record` repeatedly
  searches that list for each part and can write base filenames for each BOM row.
  Large parts/files counts and many separate writes can make this expensive.
- `PdmService.get_item_structure_project` reads all project Items/usages and
  recursively assembles a full structure payload. Collapsed UI branches avoid
  creating all Qt rows immediately, but their data has already been fetched.

Recommended order:

| Next improvement | Intended behavior | Important constraint |
| --- | --- | --- |
| Load the first visible BOM level from indexed database queries | Show roots first; fetch children when a branch is expanded; page large root lists and search results. | Search and exports must still cover the complete project, including unloaded branches. |
| Incremental file verification | Reuse the last scan result; recheck files affected by check-in, merge, associations, or external changes. Build one filename index per scan; compare size/modified time before hashing. | Preserve saved validation/deliverable states. Show a separate last-checked time; external changes still need periodic or manual reconciliation. |
| Reduce database round trips and writes | Batch metadata reads; update only changed base names in short transactions; review query plans and existing indexes. | Keep ownership, revision binding, and multi-user consistency rules. |
| Optional local read cache | Show a cached structure promptly, then refresh using the project's event/change counter. | Clearly identify stale data; revalidate locks and permissions against the authoritative database before changes. |

My first recommendation is **database-backed loading of roots and expanded
branches, followed by incremental file checks**. Moving the full scan to a worker
alone cannot remove its total cost. Qt's
[fetch-more model support](https://doc.qt.io/qt-6/qabstractitemmodel.html#fetchMore)
provides a path for larger tree models, and SQLite's
[query-planning guide](https://www.sqlite.org/queryplanner.html) explains how
indexes support selective reads.

If measurements show that many clients accessing the shared SQLite file are the
main bottleneck, consider a server/API next: keep the database beside the server
and exchange query results with clients. This is a larger architecture decision;
SQLite documents the extra file traffic and latency of direct
[network filesystem access](https://www.sqlite.org/useovernet.html).

## Build status

`config.APP_VERSION` is **4.3.0**. No schema migration is required for these
startup changes. Updating source does not update an already built EXE folder:
build and publish 4.3.0 using the normal build/release workflow. The existing
4.2.0 distribution remains the previous build until that is done.

## Verification

Fixture tests exercise the real toolbar and BOM panel, a deliberately delayed
structure query with a running GUI timer, stale-result suppression, project
switching during a load, failure/retry, deferred module creation, and closing
during a load. All database work in these tests uses a temporary fixture.
These checks are not a timing benchmark of the company project.

A wider run also found seven existing `test_pdm_tree_ui` errors caused by missing
helpers in its test harness. The same seven errors were reproduced using the
unchanged HEAD version of `pages/bom_page.py`; they are not fixed by this change.
