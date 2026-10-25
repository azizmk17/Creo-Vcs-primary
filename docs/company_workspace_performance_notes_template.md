# Company workspace performance notes

## Test conditions

- Date/time and timezone:
- App version/build:
- Runtime: installed EXE / native Python
- PC label (e.g. Laptop A):
- CPU / RAM / Windows version:
- App installed on local SSD or network share:
- Connection: company LAN / company Wi-Fi / VPN
- Database location: local / shared network folder
- Database size:
- CAD/project directory location: local / shared network folder
- Project/version label (use an alias if confidential):
- Project/version numeric ID:
- Approximate Item count / CAD file count / largest assembly size:
- Approximate active users:
- Creo and other heavy applications open:
- PDF and STEP file sizes used in the test:
- Any changes in network, workload, or project data between runs:

## Visible operation times — seconds

Use a stopwatch. Start each run with the app fully closed. Do not include time
spent typing credentials, selecting a project/version, or choosing a file.
Record first versus repeated opens separately. Leave an untested cell blank.

| Action | First session | Warm 1 | Warm 2 | Warm 3 | Warm 4 |
| --- | --- | --- | --- | --- | --- |
| EXE launch → login ready | | | | | |
| Login submit → main window usable | | | | | |
| Load Project → first BOM usable | | | | | |
| Load Project → background checks complete | | | | | |
| First expansion of selected assembly | | | | | |
| Re-expansion of same assembly | | | | | |
| Select Item → details/badges ready | | | | | |
| Open Managed Files → list ready | | | | | |
| First PDF → readable | | | | | |
| Repeat PDF → readable | | | | | |
| First STEP → viewer usable | | | | | |
| Repeat STEP → viewer usable | | | | | |
| Timeline/branch files → list ready | | | | | |
| Search → results usable | | | | | |
| Refresh → BOM usable | | | | | |
| Switch project version → first BOM usable | | | | | |

## Problems and observations

- Which operation was slowest?
- Was the app responsive while the background checks ran?
- Any complete UI freeze? Approximate duration:
- Any timeout, error, or incomplete scan? Keep the error text and failed report:
- Was the first open much slower than subsequent opens?
- Could you begin working before all file checks completed?
- Any separate test-Item write workflow measured? Describe start/end points:
- Anything else that changed during the test:
