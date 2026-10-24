# Startup and project loading — Nexus 4.4.0

## Open a project

1. Sign in. The application shell opens before project structures/file checks.
2. Select the **PRODUCT** and **VERSION** in the toolbar.
3. Click **Load Project**. Selecting a dropdown alone starts no load.
4. The BOM loader remains visible until the first root page is ready.
5. Expand an assembly to fetch its direct children. A branch spinner shows while
   that query runs. Expand **Load next 200 items…** for another page of roots or
   children in a large level. A failed branch can be expanded again to retry.

First login does not load a default version. Later logins can restore a valid,
explicitly saved project/version. Commit Workspace, Issue Center and Manager
Dashboard are still constructed on first use, as introduced in 4.3.0.

## What changed in 4.4.0

The EBOM worker reads a maximum of 200 live roots and their display metadata.
It does not read every descendant or every CAD Document before presenting the
first page. A branch expansion queries only its next 200 direct children using
the existing Item/usage indexes. Separate occurrences of reused subassemblies
retain their own ancestor paths, levels and effective quantities. Circular
structures are rejected during expansion.

Each asynchronous request captures its project. Refreshing, switching projects,
or closing cancels obsolete work and discards late results. CAD associations
and drawings remain available through the existing Show CAD action. Opening
the CAD Structure view still loads its complete structure in a worker.

Search and advanced filters query the complete project in SQL, including
unloaded branches. Status/type filter choices also come from the database.
Exports, comparisons, variant navigation and explicit whole-tree actions retain
the complete-structure API; an action requiring the complete tree can take
longer than opening a single branch. Large filter/search result rendering and
the CAD Structure view are not paged by this change.

Opening an organizational folder loads a complete data snapshot so folder
members on unloaded pages are included. It materializes the visible ancestor
chain, rather than creating every Qt row in the project.

Background directory checks now build a filename/version index in one pass.
The initial diagnostic worker shares that index between BOM-file checks and
unexpected-file checks. Exact base-name lookups replace repeated directory-list
searches for every Item. Creo suffixes compare as integers, so `.10` is newer
than `.9`. Forced-integration exemptions retain their existing behavior.

The synchronizer writes only changed base filenames, in batches of at most 250.
Each update checks that the source native filename/drawing has not changed since
it was read, so a concurrent check-in is not overwritten by a stale diagnostic.
Snapshot/approval allowlists use sets instead of repeated nested searches.
Reading a PDF/STEP badge's legacy path no longer loads full Item details and
assembly binding calculations.

These are filesystem observations and metadata repairs. They do not write Item
lifecycle, PDF/STEP acknowledgements, validation states, or release approvals.
A missing file can still produce a diagnostic warning under the existing rules.

## Benchmark and reproducibility

The fixture **BENCHMARK - Company 11000**, version **A**, has 10,000 parts,
1,000 assemblies, 2,750 drawings, 35,750 copied files and 12,942 expanded Item
occurrences. It occupies about 2.51 GB of file content. See the
[dataset guide](../tools/README_benchmark_project.md) for its synthetic CAD
limitations and disk protections.

The [4.3.0 baseline](benchmarks/company_11000_before.json) demonstrates the two
original costs: 12,942 Item occurrences and 13,750 CAD Documents in the initial
payload, and 9,140 unchanged-metadata writes before the file check was cancelled
at 120 seconds. The baseline did **not** finish all 11,000 Items, so 120 seconds
is a lower bound, not a full-scan time. Updated raw measurements are saved beside
it in `docs/benchmarks/`.

Three serial 4.4.0 runs produced these local component results:

| Component | 4.3.0 baseline | 4.4.0 median (range) |
| --- | ---: | ---: |
| EBOM structure worker | 2.286 s | 0.269 s (0.239–0.287) |
| Initial Qt row construction | 2.356 s | 0.522 s (0.501–0.543) |
| BOM file synchronization | Unfinished at 120 s; 9,140 items checked | 1.689 s (1.679–2.471); all 11,000 checked |
| Working-directory check | 3.207 s | 0.380 s (0.376–0.382) |
| Initial Item occurrences / CAD Documents | 12,942 / 13,750 | 10 / 0 |
| Metadata write attempts in file synchronization | 9,140 before cancellation | 0 |

See the [three-run summary](benchmarks/company_11000_summary.json) and individual
`company_11000_after_1.json` through `company_11000_after_3.json` reports. The
source company database's SHA-256 was unchanged across all three runs. These
component times are not whole-application startup times.

Three 4.5.0 runs on the same fixture measured service setup at **0.394 s**
(0.329–0.455) and BOM widget setup at **0.436 s** (0.435–0.649). The structure
worker measured 0.313 s (0.285–0.565), initial row creation 0.799 s
(0.712–1.125), file synchronization 2.500 s (2.478–2.643), and the working
directory check 0.539 s (0.493–0.574). File synchronization checked all 11,000
Items with zero metadata writes. Timing varied by component and run; compare the
raw 4.5.0 reports (`company_11000_optimized_graph_2.json` through
`company_11000_optimized_graph_4.json`) and repeat on the target computer before
using these local measurements to estimate deployment performance.

Run the following from the repository root in the normal app Python environment:

```powershell
python tools/benchmark_project_loading.py `
  --database 'C:\Users\mkazi\shared_folder(I)\creo_vcs.db' `
  --project 8 `
  --output docs/benchmarks/company_11000_after.json
```

Replace Python, database path, project ID and output path on another computer.
The tool opens the supplied database read-only and backs it up to a temporary
database. All measured operations, including metadata updates, target that copy.
The native working directory is read in place. Each output records component
times, payload counts and write attempts; the file check has a 120-second limit
by default (`--max-scan-seconds`). Temporary database files are removed afterward.

These are local, warm-cache component measurements with a hidden Qt widget.
They measure database worker work, row construction and diagnostics separately;
they do not measure login, first painted frame, network latency, or Creo geometry.
Do not treat immediate post-generation runs as cold-cache results. Use several
serial runs and record median/range. Repeat on the actual company share before
making network deployment claims.

## Remaining performance work

Directory enumeration is still fresh for each diagnostic pass. There is no
persistent inventory/read cache and no cached result that could hide external
file changes across application runs. Full-file hashing is not part of these
routine presence/version checks.

Measure the new path on the company share before adding persistent per-workspace
inventory, changed-file scheduling, or a shared scan coordinator. Any future
cache needs workspace/project/version/user/machine scope, visible freshness,
event invalidation and periodic reconciliation. Lock and permission checks must
continue to use the authoritative database before changes.

If shared SQLite network traffic remains dominant after selective reads, a
server/API colocated with the database is a separate architecture option.

## Version, build and verification

The source version is **4.5.0**. The startup optimizations do not require a
database schema migration. See [Packaged startup measurements](packaged_startup.md)
for the login and BOM initialization measurements and isolated build procedure.

Fixture tests cover root/child pagination, reused-component quantities, search
through unloaded branches, branch retry, stale-result suppression, cycle
detection, numeric Creo versions, external deletions, unchanged-metadata scans,
cancelled scans and concurrent filename changes. Existing startup/selector,
PDF/STEP acknowledgement and PDM workflow checks remain applicable.

A wider 90-test run passed 87 checks and exposed three pre-existing PDM service
test failures: migration association-count idempotence, CAD member-removal build
expectations, and an export test expecting `source` instead of `usage_source`.
All three reproduce when the unchanged HEAD `PdmService` implementation is used.
The six new GUI loading cases and four inventory/metadata cases pass separately.
