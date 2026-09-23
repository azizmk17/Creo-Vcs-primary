# Nexus PDM for Creo 3.0

This synchronous J-Link application connects Creo Parametric 3.0 M020 to the
currently running Nexus desktop application. The Java code never opens the
Nexus database. It uses an authenticated localhost API, so the existing Nexus
permission, lifecycle, checkout, Item, CAD Document, and audit rules remain the
authority.

## Current commands

- **Connect to Active Project** shows the Nexus user and selected product version.
- **Select CAD Workspace** selects or creates a machine-local managed workspace.
- **Retrieve** lists CAD Documents from the active Nexus product version, then copies
  the selected controlled CAD file, related drawings, and recursive managed assembly
  dependencies into the selected local CAD workspace. Dependencies remain read-only
  unless they have their own checkout in the same user, workspace, and machine context.
- **Status** shows revision, iteration, lifecycle, checkout owner, and editability.
- **Workspace Status** shows every Creo model file in the selected workspace,
  including unmapped local files, plus active Nexus checkouts.
- **CAD History** shows the append-only checkout, check-in, and undo history for
  the current CAD Document.
- **Check Out** obtains the Nexus CAD lock plus an explicit checkout for every
  associated Item, then opens the editable workspace copy. The Item checkout is
  visible and independently owned by the current user rather than being a hidden
  CAD-only reservation.
- **Check In** shows a workspace checklist that starts with every Creo file found in
  the selected local CAD workspace, then marks checked-out CAD Documents and managed
  models currently loaded in Creo, including loaded children/dependencies. The user
  selects which eligible CAD Documents to stage and one shared comment is applied.
  Ctrl/Shift selection and Ctrl+A are supported; **Set Selected** applies Check in or
  Skip to the highlighted rows, while **Set All** applies the action to every eligible row.
  Before staging starts, a server-side preflight checks ownership, workspace identity,
  revision/iteration baseline, required dependencies, modified dependencies omitted
  from the selection, and duplicate Pending files. All findings are resolved together
  in the multi-object **Conflicts** window.
  If the user already owns a Pending commit, Creo asks whether to add the files to
  that group or create a new one. If a model is already pending, its conflict row
  offers Replace Pending Copy or Skip Object. The selected files then appear in the
  Commit page's Pending section. CAD and associated Item checkouts remain active
  until the Pending commit is approved and merged; merge creates the managed CAD
  iteration and closes the coordinated checkouts.
- **Undo Check Out** releases the coordinated checkout and keeps local files read-only.
- **Create CAD Revision** creates the next CAD revision after the working copy is
  checked in or undone.
- **Release CAD Document** promotes a checked-in CAD Document through the Nexus
  lifecycle. Released data must be revised before it can be edited again.

Before a managed read-only model is changed, the J-Link guard runs at the Creo
command boundary. It covers feature edit/redefine/delete/suppress/resume and
dimension-edit commands. The Windchill-style **Conflicts** window has Object,
Description, Action, and Name columns, status icons, a full-description pane,
per-row actions, multi-selection, Set Selected, and Set All. Depending on server
state, it offers:

- **Check Out Now**: obtains the Nexus lock before the Creo command continues.
- **Continue Locally**: records local edit intent, makes only the workspace copy
  writable, and allows local Save while keeping Nexus check-in blocked. This remains
  available when another user owns the checkout; it never transfers or bypasses that lock.
- **Revise and Check Out Now**: creates the next revision for released CAD and obtains
  the coordinated CAD and Item checkout.
- **Make Read-only**: cancels the attempted managed modification.
- **Cancel**: cancels the Creo edit command.

Creo 3.0 can report a loaded child as modified after some feature-edit button
workflows have already started. The integration also checks loaded models after
guarded commands; an unauthorized modified child immediately raises the same
conflict workflow and keeps Nexus check-in blocked. Save uses the same Conflict
Management window; Continue Locally permits Save but never Rename.

Edit protection uses three coordinated layers:

- Creo command brackets stop known part, assembly, sketch, annotation, dimension,
  note, symbol, view, parameter, relation, suppress, delete, and redefine commands.
- Session-wide model, feature, and solid listeners stop feature creation/redefinition,
  component-constraint changes, model or feature parameter changes, deletion,
  suppression, and unit conversion.
- Post-command and post-event scanning catches a model that Creo marks modified only
  after an operation has started. This fallback opens Conflict Management and keeps
  Save and Nexus check-in blocked until the user checks out, continues locally, or
  undoes the unauthorized change.

Mode-specific Creo commands are rebound after model display and window changes, so
drawing and assembly commands that do not exist during J-Link startup are protected
when those modes become active.

Conflict Management never erases, retrieves, displays, closes, activates, or switches
a Creo model. **Check Out Now** updates the Nexus checkout while retaining the exact
model and window. **Cancel** and **Make Read-only** cancel only the attempted command.
Display and window lifecycle callbacks rebind command guards but never open conflicts.

When local edit intent is later checked out, Nexus preserves the local bytes and
uses the controlled server file as the comparison baseline, preventing silent
overwrites of local work.

Managed files that are not checked out by the current Nexus user in the assigned
workspace are read-only. Command guards also block Save and Rename when checkout
ownership cannot be verified. Backup/Save As remains available because it creates a
separate copy without modifying the controlled source model. Unmanaged Creo work
outside a Nexus workspace is not blocked.

The integration follows a Windchill-style rule: the Nexus server owns identity,
revision, lifecycle, permissions, checkout locks, Item coordination, and audit
history; Creo owns the active model session and the local workspace copy. A local
file is never accepted for check-in only because its name looks correct: it must be
inside the selected machine-local workspace and match the active Nexus project and
CAD Document identity.

The unified edit, dependency, checkout, and check-in conflict rules are in
[CONFLICTS.md](CONFLICTS.md).

## Installed paths

- JDK: `C:\Program Files\Java\jdk1.7.0_80`
- Runtime: `C:\Program Files\Java\jre7`
- J-Link API: `C:\Program Files\PTC\Creo 3.0\M020\Common Files\text\java\pfc.jar`
- Creo launcher: `C:\Program Files\PTC\Creo 3.0\M020\Parametric\bin\parametric.bat`

No Creo Object TOOLKIT Java installation or license is used.

## Build and run

1. Close every existing Creo session.
2. Start Nexus normally.
3. Sign in and select the required product and version in Nexus.
4. Run `compile.bat` once, or whenever Java source changes.
5. Run `run_creo.bat`.
6. In Creo, open **Applications > Nexus PDM > Connect to Active Project**.
7. Select a CAD workspace before Retrieve or Check Out.

`build_and_run.bat` combines steps 4 and 5.

The Nexus bridge descriptor is generated for the signed-in Windows account at:

```text
%LOCALAPPDATA%\CreoVCS\bridge.json
```

It contains a random per-process token and a `127.0.0.1` API address. The file is
removed when Nexus exits normally. The token is never stored in `protk.dat` or
the Java build.

## Moving this directory

`protk.dat` contains absolute paths because that is the most reliable registration
method for this Creo release. If the repository moves, update these two entries:

```text
java_app_classpath  <new-directory>\classes
text_dir            <new-directory>\text
```

Update the Java and Creo paths in `compile.bat`, `run_creo.bat`, and `config.pro`
only if those products are installed elsewhere.

## Troubleshooting

- **Application is not registered:** start Creo with `run_creo.bat`; do not attach
  to an already running Creo process.
- **Startup failed:** run `compile.bat`, verify `protk.dat` paths, and inspect
  `std.out` plus the newest `trail.txt.*` in this directory.
- **Nexus bridge is not running:** keep Nexus open, sign in, and wait until its main
  product window is visible.
- **Wrong project or unmanaged file:** select the product version in Nexus that owns
  the CAD Document, then use Status again.
- **Retrieve list is empty:** select the product version in Nexus that owns the CAD
  Documents, then use Retrieve again.
- **File remains read-only:** check it out into the same CAD workspace from which it
  is opened. Files checked out by another user intentionally remain blocked.
- **An edit command is not intercepted:** Creo identifies commands by internal
  command IDs. Enable `auxapp_popup_menu_info yes`, reproduce the edit once, and
  inspect the newest trail file if a custom Creo command must be added to the
  guard list in `NexusJLink.java`.
