# Company workspace performance test

Use a Nexus **4.9.0 or newer** build containing the **Performance** recorder, the actual company
database location, and a representative company project and version. The
recorder works in the installed app; users do not need Python, Anaconda, a
command prompt, or a separate benchmark project.

The existing 4.8.0 EXE does not contain this recorder. Build 4.9.0 using
[the Windows build guide](../README_BUILD_EXE.md), then deploy that build to the
company computer. For an existing managed installation, follow the signed
update process in [Release, installation, and updates](../README_RELEASE_UPDATES.md).
For a fresh installation, extract the complete built folder and run `Setup.exe`.
The recorder itself introduces no database migration.

## Recommended: record and export from Nexus

1. Open Nexus. On the sign-in screen, click **Performance recording…**.
2. Select **Company network / Wi-Fi** or **VPN** and the session type. Click
   **Start recording**, then **Close**. The recording continues in the background.
3. Sign in. Select the product and version you want to measure, then load it.
4. When the BOM is ready, expand the same large assembly, select several items,
   open an item's Structure and Managed Files tabs, and perform a search. Wait
   for the project file check to finish. You can also open Commit and Issues.
   For checkout, check-in, or merge timings, use an appropriate test item and
   follow your company's normal workflow.
5. Click **Performance: Recording** on the top toolbar and select
   **Stop and save report…**. Choose the Desktop or another **local** folder.
6. Send the resulting `Nexus-performance-....zip` file for analysis. The ZIP
   contains `summary.txt`, `performance.json`, and a short README. No command
   is required.

You can also start recording from the **Performance** button after signing in.
Starting before sign-in records more of the session. If Nexus is closed while a
recording is active, it offers to save the report; canceling the save leaves the
recording running.

For a useful comparison, repeat the same sequence at least three times using
the same project/version and assembly. Mark the first session and subsequent
sessions using the **Session** selector. Repeat separately over VPN if that is
relevant. Use a different ZIP for each run; keep failed or unusually slow runs.
The selector is your description of the session, not proof that Windows or the
server cache is cold.

### What the report contains

The recorder includes the app version, Python/frozen runtime, Windows version,
UTC start time, your selected connection/session type, startup milestones,
operation durations, aggregate SQLite connection/statement/fetch timings and
counts, selected file-check timings, event-loop timer delays, and periodic CPU
and memory samples for the Nexus process. It also
captures named background checks, BOM loading, item details, expansion, search,
and instrumented checkout/commit/merge operations when they actually occur.

It does **not** copy the database, CAD files, PDFs, or STEP files. It does not
include SQL text or parameters, item names, project names, user names, or file
paths. You may review `summary.txt` and `performance.json` before sharing.

Interpretation limits:

- Startup milestones begin at the Python entry point; they do not include all
  operating-system process launch and EXE loading time. They may start before
  the recording button was pressed. No database calls before recording began
  are included.
- SQLite timings cover connections opened through the app's instrumented
  connection wrapper while recording is active. Existing connections, custom
  factories, SQLite cursor iteration, and some implicit operations may be
  absent. Execution, fetch, connection, and transaction durations can overlap.
  They are wall time, not separate measurements of network, disk, CPU, or locks.
- File timings cover instrumented checks and scan operations, not every file
  operation in Nexus or Creo. Event-loop delay includes OS scheduling, sleep,
  and backgrounding; it is not proof of an app freeze.
- CPU percentage is relative to one logical CPU, so a multi-threaded process
  may exceed 100%. The working-set value is sampled Windows physical memory,
  not a continuous peak. Neither includes separate Creo or CAD Viewer processes.
- Recording adds overhead. Operations still in progress at export may be absent;
  wait for the activity you want to measure to finish. Histograms use the upper
  bounds in the JSON; a missing metric means it was not observed.

## Optional: older external measurements

## What these measurements cover

Collect both kinds of evidence:

| Evidence | What it measures | Important limit |
| --- | --- | --- |
| Normal interactive session + startup trace | Real startup/workspace/license checks and the visible experience using the company database and project | The startup trace has no detailed BOM/query/file-operation timings; record the actions below separately |
| EXE component benchmark JSON | BOM service setup, structure loading, initial row creation, GUI refresh, file synchronization, integrity checking, and controlled metadata writes | Database operations run on temporary local copies. Project files are read from their configured location. This is not a measurement of shared-database query/write latency |

Do not treat the component JSON as an end-to-end company-network result. The
in-app recording above is more representative of an interactive company run.
The component runner also uses a hidden Qt view and the first assigned project
user for its test session. Its view and permissions may differ from those of the
person doing the interactive test. Keep these limits in mind when comparing runs.

## 1. Prepare the test

1. Use the exact app build that will be deployed, installed on the laptop's
   local disk. Record its version. Before first deployment of a newer version,
   follow the workspace's normal backup and administrator migration procedure.
2. Select one representative large project and its version. Use the same pair
   throughout the repeated runs.
3. Record whether you are on company LAN/Wi-Fi or VPN, where the database and
   CAD directories are stored, and approximately how many other users are active.
   Test LAN and VPN separately if both are relevant.
4. Close other Nexus instances before each startup measurement. Keep conditions
   comparable; record whether Creo and other heavy applications are open.
5. Copy `company_workspace_performance_notes_template.md` into your local results
   folder as `notes.md` and fill it in during the test.

Keep results on the laptop, not on the shared drive being measured.

## 2. Record a normal company-workspace session

Open **PowerShell**. Change only the EXE path and run label below. Run this once
for the first session of the day, then repeat with labels `LAN-warm-01` through
`LAN-warm-04`. Close the app completely between runs. A first session after a
restart is useful, but it is not proof that every Windows/server cache is cold.

```powershell
$exe = 'C:\PATH\TO\Nexus.exe'
$results = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nexus-performance'
$runLabel = 'LAN-first'
New-Item -ItemType Directory -Force -Path $results | Out-Null

$env:NEXUS_STARTUP_PROFILE_FILE = Join-Path $results ($runLabel + '-startup.json')
$env:NEXUS_STARTUP_PROFILE_EACH_STAGE = '1'
Remove-Item Env:NEXUS_STARTUP_PROFILE_EXIT_AT_LOGIN -ErrorAction SilentlyContinue
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
try {
    $process = Start-Process -FilePath $exe -PassThru
    $process.WaitForExit()
} finally {
    Remove-Item Env:NEXUS_STARTUP_PROFILE_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:NEXUS_STARTUP_PROFILE_EACH_STAGE -ErrorAction SilentlyContinue
}
```

Sign in normally, choose the real project and version, and perform this same
sequence each time. Use a stopwatch and record seconds in `notes.md`:

| Action | Start timing | Stop timing |
| --- | --- | --- |
| EXE startup | Launch the EXE | Login screen is visibly ready |
| Login | Submit credentials | Main window accepts input |
| Initial BOM | Click Load Project after choosing project/version | First BOM rows are visible and usable |
| Background checks | Click Load Project | The project file-check completion message appears |
| First assembly expansion | Expand the same large assembly | Its child rows are visible and usable |
| Repeated expansion | Collapse and expand that assembly again | Child rows are usable |
| Item details | Select the same representative part | Details and document indicators finish updating |
| Managed Files | Open the Managed Files tab | Current files are visible |
| First PDF preview | Open/preview the same PDF | PDF is readable |
| First STEP view | Open the same STEP | Viewer is ready to use |
| Timeline/branch files | Open the timeline or its file viewer | The requested list is ready |
| Search | Submit the same search | Results are usable |
| Refresh | Click the normal BOM refresh | Updated rows are usable |
| Version switch | Confirm loading another chosen project version | Its first rows are usable |

Record first-time PDF/STEP loads separately from subsequent opens. Include file
sizes, not the files themselves. Note any freezing, timeout, unexpected rescan,
or operation still running after the first BOM appears.

The startup JSON records internal checkpoints including workspace/license gates,
first login paint, and shell construction. It also includes time spent waiting
for user actions, and its first checkpoint may occur after initial EXE/DLL
loading. Do not add user typing time to app processing time or treat the first
startup event as a measurement of the entire OS process launch.

For write workflows such as checkout/check-in/commit/merge, use a dedicated test
Item or an isolated test project. Record them separately from the viewing tests.
The component benchmark's metadata rewrite does not measure real shared-DB
writes and should not be used as a substitute for that workflow test.

## 3. Export the existing component benchmark

Find the selected project/version's numeric **ID** in **Admin → Projects**. Each
project version has its own ID. It is not the project name or the CAD revision.

Run the following in PowerShell, replacing the EXE path, database path and ID.
It produces three separate JSON reports, one run at a time. This mode has no
interactive window and may take several minutes on a large network directory.

The source database is opened read-only and backed up with SQLite's backup API.
Measured changes target temporary local databases. The CAD directory is read in
place. Allow local free space for **two database copies plus temporary overhead**;
CAD files are not duplicated by this benchmark. It still creates network read
traffic, so record the workload conditions during the runs.

```powershell
$exe = 'C:\PATH\TO\Nexus.exe'
$database = '\\SERVER\SHARE\creo_vcs.db'
$projectId = 123
$results = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nexus-performance'
New-Item -ItemType Directory -Force -Path $results | Out-Null
$env:NEXUS_ENABLE_PERF_BENCHMARK = '1'
try {
    foreach ($number in 1..3) {
        $output = Join-Path $results ('components-' + $number + '.json')
        $arguments = '--performance-benchmark --database "{0}" --project {1} --output "{2}" --max-scan-seconds 600' -f $database, $projectId, $output
        $process = Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru `
            -RedirectStandardOutput (Join-Path $results ('components-' + $number + '-stdout.txt')) `
            -RedirectStandardError (Join-Path $results ('components-' + $number + '-stderr.txt'))
        if ($process.ExitCode -ne 0) {
            throw ('Component run ' + $number + ' failed. Keep its logs for analysis.')
        }
        if (-not (Test-Path -LiteralPath $output)) {
            throw ('Component run ' + $number + ' did not produce JSON. Keep its logs for analysis.')
        }
    }
} finally {
    Remove-Item Env:NEXUS_ENABLE_PERF_BENCHMARK -ErrorAction SilentlyContinue
}
```

The 600-second option limits the normal file-synchronization phase only. It is
not a timeout for the complete benchmark or its separate metadata-rewrite phase.
Reports marked `file_sync_timed_out` are incomplete scans, not successful full-scan
timings.

If only native Python is being used, the equivalent single component run is:

```powershell
python tools/benchmark_project_loading.py --database '\\SERVER\SHARE\creo_vcs.db' --project 123 --output 'components-native-1.json' --max-scan-seconds 600
```

Compare native Python and the EXE only on the same laptop, project version,
database snapshot, app version, and network connection. Keep results labelled
by runtime and do not compare a first run against a later cached run.

## 4. Export and return the results

Keep failed or unusually slow runs; do not keep only the fastest result.
Review notes and logs for company-specific paths/names before sharing. These
reports do not need CAD files, database contents, passwords, licence files, or
signing keys.

```powershell
$results = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nexus-performance'
$zip = Join-Path (Split-Path $results -Parent) ('Nexus-performance-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
Compress-Archive -Path (Join-Path $results '*') -DestinationPath $zip
$zip
```

Send the ZIP containing startup JSON, component JSON/logs, and `notes.md`.
The analysis can compare first versus repeated runs, medians and ranges,
component bottlenecks, scan completeness, and the gap between local-copy
benchmarks and the actual shared-workspace experience. The in-app recorder
groups SQL timings by operation type; it does not save individual SQL text.
