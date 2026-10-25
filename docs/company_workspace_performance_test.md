# Company workspace performance test

Use the installed Nexus EXE, the actual company database location, and an actual
company project/version. No Python or Anaconda installation is required for the
EXE commands below. Keep the complete EXE/DLL installation together.

## What these measurements cover

Collect both kinds of evidence:

| Evidence | What it measures | Important limit |
| --- | --- | --- |
| Normal interactive session + startup trace | Real startup/workspace/license checks and the visible experience using the company database and project | The startup trace has no detailed BOM/query/file-operation timings; record the actions below separately |
| EXE component benchmark JSON | BOM service setup, structure loading, initial row creation, GUI refresh, file synchronization, integrity checking, and controlled metadata writes | Database operations run on temporary local copies. Project files are read from their configured location. This is not a measurement of shared-database query/write latency |

The current app does **not** have a complete live-session performance recorder
or a one-click performance ZIP export. Do not treat the component JSON as an
end-to-end company-network result. An in-app recorder would be needed for
automatic timing of live SQL, UI stalls, and every user operation.
The component runner also uses a hidden Qt view and the first assigned project
user for its test session. Its view and permissions may differ from those of the
person doing the interactive test. Keep these limits in mind when comparing runs.

## 1. Prepare the test

1. Use the exact app build that will be deployed, installed on the laptop's
   local disk. Record its version. Before first deployment of a newer version,
   take the normal database backup because ordinary app startup may apply its
   database migrations.
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
benchmarks and the actual shared-workspace experience. Detailed attribution of
that gap to individual live SQL queries still requires a live-session recorder.
