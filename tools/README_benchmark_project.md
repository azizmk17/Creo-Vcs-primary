# Large project for loading measurements

`create_benchmark_project.py` creates a **separate synthetic project** in an existing
Nexus database. It copies numbered Creo files from a registered source project.
It requires Python 3.10 or newer and uses only the standard library; Creo, OCC,
PDF libraries and a packaged application build are unnecessary.

## Default dataset

| Content | Count |
| --- | ---: |
| Unique parts | 10,000 |
| Unique assemblies | 1,000 |
| Product roots | 10 |
| Maximum BOM levels, including parts | 4 |
| Drawings, one per selected item | 2,750 |
| Native model iterations per item | 3 |
| Total copied working-directory files | 35,750 |
| Persisted parent/child relationships | 12,802 |
| Expanded BOM occurrences, including reused assemblies/components | 12,942 |

The generator registers Items, revisions and iteration snapshots, CAD Documents
and their iterations, owner/drawing associations, CAD members, persisted Item
usages, legacy BOM links, exact iteration bindings and structure snapshots.
Items start in **Design / In Work** with no checkouts or approval claims. The
new project has version **A** and inherits source-project user membership without
changing anyone's current project. Existing data and schema are not migrated.

**These are copies, not newly authored CAD models.** Creo assembly and drawing
references inside the binaries are unchanged. The database hierarchy is synthetic
and does not describe the internal geometry of those files. Use this fixture for
Nexus project loading, BOM rendering, inventory and metadata performance, not for
Creo regeneration, geometry comparisons, or release validation. Different native
iterations may contain identical bytes when the source has only one iteration.
No PDF/STEP deliverables, approvals, commits, releases, or extra master copies are
created. The fixture does not simulate the geometry size or full history of a
large production database.

## Run on this computer

Run from the repository directory in PowerShell. This first command only reports
the plan; it writes no database records or files:

```powershell
& C:/Users/mkazi/miniconda3/envs/pyoccenv/python.exe tools/create_benchmark_project.py `
  --database 'C:\Users\mkazi\shared_folder(I)\creo_vcs.db' `
  --source 'C:\Users\mkazi\shared_folder(I)\working_directories\local_project' `
  --destination 'C:\Users\mkazi\shared_folder(I)\working_directories\benchmark_company_11000'
```

Add `--apply` to execute it. On another computer, use that computer's Python and
absolute database/source/destination paths. The source must match exactly one
project registered in that database. This utility supports schema version **49**
and refuses other versions instead of modifying their schema.

The project name defaults to **BENCHMARK - Company 11000**. To create a different
dataset, supply `--name`, `--parts` and `--assemblies`, and use a fresh destination.
Both counts must be positive. Existing names and destinations are rejected; a
second run cannot overwrite or silently duplicate the first.

## Disk protection and verification

With the selected source files, the default dataset has approximately **2.51 GB
of file content**, approximately **2.61 GB allocated at 4 KiB clusters**. The
preflight budget is about **3.21 GB**, including conservative database, backup,
journal and manifest allowances. It refuses plans over **4 GiB**, or plans that
would leave less than **6 GiB free**. It also monitors free space while copying.
Other applications can consume space concurrently, so the runtime guard can stop
a run. There are no hardlinks or symlinks; each target is a physical copy.

Every copied file is checked against the source SHA-256. Before database writes,
the SQLite backup API creates a consistent sibling backup named
`creo_vcs.db.before_benchmark_<dataset-id>.bak`. Registration uses one transaction
and checks for new foreign-key violations and SQLite integrity errors before
commit. It does not change the shared database's journal mode.

The destination's **benchmark_manifest.json** records source-template hashes,
counts, project ID, backup location, disk estimates and completion state. Keep it
with the dataset. A failed run leaves its owned files and a failure marker for
inspection; it does not automatically delete directories or restore the whole
database. Check whether the recorded project ID committed before retrying.
Do not restore an old backup over a database containing newer company work.

## Open and measure later

1. Use the shared database above in Nexus. Refresh the project selector, or reopen
   the application if the new project is not yet listed.
2. Select **BENCHMARK - Company 11000**, version **A**, then **Load Project**.
3. Record separately the time to the first usable BOM, background inventory
   completion, root expansion, CAD view loading and search results.
4. Compare the unchanged dataset before and after performance changes. Record
   app version, computer, storage location, dataset ID and whether the OS cache
   was warm. File generation and hash verification warm the OS cache; the first
   immediate load after generation is not a cold-storage test. Restarting only
   the app does not clear the OS cache.
5. Use several repeated runs and report the median and range. Keep network-share
   results separate from local-disk results. File count and byte volume are both
   relevant; this fixture emphasizes file count and BOM size.

Creation duration in the manifest is **not** application load time. The generator
does not start the app, build an executable or implement loading optimizations.

For the measurement command, before/after component timings and the 4.4.0
loading improvements, see [Startup and project loading](../docs/project_loading.md).
The measurement utility runs against a temporary database copy, with the
original working directory read in place.
