# Native CAD Structure Synchronization

## Entry Point

In Nexus CAD Structure, right-click a CAD Document and choose
**Validate Structure from CAD Files...**. Select **Scan CAD**, review the change
table and conflicts, then **Apply Reviewed CAD Changes**. This is an on-demand
operation, not a model-open, regeneration, edit, or save callback.

The scanner reads the selected model recursively and the project's native
drawings. It discovers assembly membership from component features and drawing
models from Creo, never from a filename convention. Repeated components become
quantities in Nexus's existing parent/child table. Individual component feature
IDs, drawing model lists, dependency lists, and file hashes remain in the scan
evidence (`cad_structure_scans`).

New native CAD Documents can be registered during reviewed apply. No EBOM Items
are automatically created. Existing CAD-to-Item associations, product variants,
CAD revisions, PDF/STEP paths, exclusion policies, and authored EBOM rows remain
unchanged. Use the existing association and CAD-to-EBOM comparison/build commands
after reconciling the CAD structure.

## Runtime

This is a separate asynchronous **J-Link** Java process, not a synchronous
auxiliary application or a Toolkit DLL. Do not add it to `protk.dat` or mix its
classpath with `pfc.jar`. The existing interactive Nexus J-Link integration
does not need recompiling for this feature.

Defaults are Java 7u80 and Creo 3.0 M020. To select a different installed Creo
datecode, set these variables in the environment used to launch Nexus:

```powershell
$env:NEXUS_JAVA_HOME = 'C:\Program Files\Java\jdk1.7.0_80'
$env:NEXUS_CREO_COMMON = 'C:\Program Files\PTC\Creo 3.0\M020\Common Files'
$env:NEXUS_CREO_START = 'C:\Program Files\PTC\Creo 3.0\M020\Parametric\bin\parametric.bat'
$env:NEXUS_CREO_SCAN_TIMEOUT = '300'
```

Use the installation's `parametric.bat` launcher, matching `pfcasync.jar`, native libraries, and `pro_comm_msg.exe`
from one installation. The worker checks these paths and compiles its small
extractor against that installation. It sets child-process environment variables
without changing the machine's environment or installed configuration. The scan
uses its own working directory and configuration while retaining the real
Windows user profile; Creo's startup and name-service processes require that
profile to match the Java caller.

PTC documents [separate asynchronous libraries and environment setup](https://support.ptc.com/help/creo_toolkit/otk_java_pma/r11.0/usascii/creo_toolkit/user_guide/Setting_up_an_Asynchronous_J_Link_Application.html).
Its [nongraphical startup mode](https://support.ptc.com/help/creo_toolkit/otk_java_pma/r13/usascii/creo_toolkit/user_guide/Setting_Up_a_Noninteractive_Session.html)
still starts a real Creo process; it is not a license-free native file parser.
An available Creo license is required.

## Safety Rules

- Managed inputs are pinned to the recorded current iteration/path. A missing
  approved file never falls forward to an arbitrary newer file.
- Only native files directly in the project directory are discovered. No
  recursive scan of branches, Pending folders, exports, user directories, or
  local CAD workspaces occurs. Unregistered files use the highest numeric Creo
  suffix directly in that directory and appear as proposed registrations.
- All available input native files are staged as disposable copies for dependency
  resolution. Source files are never saved, regenerated explicitly, or rewritten.
  PDF/STEP files are not copied. Large projects therefore have an intentional
  on-demand staging/hash cost, separate from interactive conflict handling.
- The worker uses its own start directory and Windows process job. It uses the
  configured system `PTCNMSPORT`, or PTC's default `1239`, for both Java and the
  spawned Creo process. The worker never connects to or erases models from the user's session.
  Timeout/cancellation terminates the scan-owned process tree only.
- A successful scan is cached by exact input hashes, root and worker signature.
  Force fresh scan bypasses the cache. Changes in Nexus still produce a new
  comparison even when native scan evidence can be reused.
- Missing native dependencies, external retrieval, unsupported family instances,
  suppressed/inactive components, substitute/bulk components, retrieval errors,
  and cyclic structures prevent apply. They cannot masquerade as deletions.
- Multi-model drawings retain an existing valid primary model. New ambiguous
  drawings require explicit primary-model registration before rescanning.
  Existing Item drawing assignments that would become invalid block rebinding.
- Apply checks project read-only state, lifecycle, permissions and checkout
  ownership. A changed existing CAD must be checked out by the applying user,
  or the user must have Merge permission. Another user's lock is never bypassed.
- Apply checks input hashes and database baseline again and runs in one SQLite
  transaction. An outdated review must be rescanned. Applied evidence is retained.
- Removing a CAD member detaches generated EBOM/build-history references, but
  does not delete EBOM rows. An explicit later Build performs EBOM reconciliation.
- Previously synchronized CAD is checked for stale native files, versions and
  membership before PDM CAD-to-EBOM Build and CAD release. Projects never scanned
  retain the existing manual workflow.

## Scope and Rollout

This first rollout is review-first synchronization of **controlled project CAD**.
It deliberately does not publish local edits, Continue Locally drafts, or Pending
commit contents. After an approved merge, rescan the controlled CAD before Build
or release. Automatic Pending-group scans, a merge-time structural transaction,
full family-table/simplified-representation policy, and Item-release-wide
validation remain separate work. Do not interpret this as full Windchill parity.

Installation-level `config.sup` or startup applications can still affect Creo;
the worker cannot safely rewrite those settings. It fails closed on timeout.
Startup failures retain the PTC toolkit function, error code, and message when
Creo provides them. `XToolkitGeneralError` on `AsyncConnection_Start` usually
means the async startup command, `PRO_COMM_MSG_EXE`, `PRO_DIRECTORY`, or Creo
name service could not establish the local connection; it does not indicate a
bad CAD root. The dialog suppresses the follow-on “root missing” message when
startup itself failed.
Failure diagnostics (not native CAD copies) are retained under
`%LOCALAPPDATA%\Nexus\creo-scan-logs\<job-id>`. The dialog reports the path.

Validation: Java 7 compilation and a live M020 native scan of an installed sample
part completed successfully. Startup through `parametric.exe` alone hangs on
this M020 installation; use the PSF-aware `parametric.bat` launcher. Keep the
Creo process running under the caller's real Windows profile. Production project
database synchronization was not exercised by this sample scan.
