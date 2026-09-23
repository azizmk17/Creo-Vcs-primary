# Nexus Creo Conflict Model

This document is the conflict contract for the Nexus Creo integration.
It separates conflicts detected while the user is editing from conflicts detected
while a PDM transaction is being prepared or submitted.

## Conflict dialog

Use one multi-object dialog with these columns:

- Status icon and object identity
- Conflict description
- Resolution action
- CAD name and revision
- Workspace or checkout owner

Rows support Ctrl/Shift selection and Ctrl+A. **Set Selected** changes the action
for highlighted rows, and **Set All** changes compatible rows. The lower pane shows
the complete description for the selected conflicts. Actions must only be offered
when the server says they are valid for every selected row.

Conflicts are either:

- **Overridable**: the user can select a resolution and retry the original operation.
- **Blocking**: another operation, permission change, or data correction is required.

## Immediate edit conflicts

| Condition | Severity | Actions |
| --- | --- | --- |
| Checked in, WIP CAD is modified without checkout | Overridable | Check Out Now, Continue Locally, Make Read-only, Cancel |
| Released CAD is modified | Overridable when revise is permitted | Revise and Check Out Now, Make Read-only, Cancel |
| Checked out by another user | Overridable locally | Continue Locally, Make Read-only, Cancel |
| Checked out by this user in another available workspace | Blocking | Open Owning Workspace, Cancel |
| Checkout points to a deleted workspace | Overridable | Recover Checkout, Check Out Here, Cancel |
| Model came from the wrong project or directory | Blocking | Open Managed Copy, Cancel |
| User lacks modify permission or lifecycle disallows editing | Blocking | Make Read-only, Cancel |
| Creo reports a child/dependent modified by an assembly operation | Mixed, per object | Resolve each affected model in one dialog |

**Continue Locally** records edit intent and permits local Save, but never grants a
server lock and never permits check-in. It remains available when another user owns
the checkout. The other user's lock is not transferred, weakened, or bypassed; Nexus
check-in remains blocked until the local user obtains the real checkout.

For a conflict detected before a Creo command, Cancel cancels that command. If Creo
only reports the modification after the command, Cancel and Make Read-only keep Nexus
Save/check-in blocked; the user must undo the local Creo change, check out, or resolve
the object with Continue Locally.

Resolving a conflict must preserve the Creo session. It must not erase, retrieve,
display, close, activate, or switch any model or window. Model-display and window-change
callbacks may register mode-specific guards, but must never display a conflict dialog.

Creo edit detection combines command brackets with session-wide model, feature, and
solid action listeners. Native before-events cover feature and component redefinition,
feature creation/deletion/suppression, parameters, and unit changes. Drawing and
mode-specific UI operations are additionally guarded by their Creo commands. A final
modified-model scan catches operations for which Creo 3 exposes only an after-state.

## Checkout preflight

Before changing files or session models, validate the complete requested set:

- Current user permission and project context
- Latest allowed CAD and Item lifecycle state
- Existing checkout owner, workspace, and machine
- Released CAD and associated Item revision requirements
- Required assembly members, drawings, and family-table members
- Cross-project dependencies
- Local edit intent that must be preserved

If one object cannot be processed, show every affected object and resolve the batch
before changing any checkout state. Checkout and Item checkout remain one atomic
server operation.

## Check-in preflight

The server runs preflight for the whole selected set before staging any file. It detects:

- Object not checked out by the current user in the selected workspace
- Unsaved or missing local content, wrong source path, or identity mismatch
- Server revision/iteration newer than the workspace baseline
- Required dependency missing from the workspace
- Modified checked-out dependency not selected for check-in
- Drawing selected without its bound PRT or ASM in the same check-in batch
- New or unresolved assembly child, drawing model, or family-table member
- Cross-project or duplicate file identity
- CAD checkout and associated Item checkout no longer coordinated
- Released, obsolete, or permission-blocked object
- Existing file in a Pending commit, including its owner

Current resolutions include **Add Required Objects**, **Replace Pending Copy**,
**Skip Object**, and **Cancel**. Save/Update and comparison workflows remain future work.
Required dependencies cannot be skipped. No files should enter a Pending commit until
all selected rows have a valid resolution.

A drawing is never staged alone. Its bound PRT or ASM must be checked out in the same
workspace and included in the selected batch. The conflict dialog can add that model
automatically; staging always processes models before drawings. The commit service
also verifies that both files resolve to the same Pending commit.

## Out-of-date detection

Each workspace manifest entry should record the server CAD ID, revision, iteration,
content hash, and retrieval timestamp. Check-in compares that baseline with the latest
server state. If the server advanced, Nexus blocks check-in and offers update or
compare; it must never silently overwrite the newer server iteration.

## Event history

Every PDM operation should create an event with operation, workspace, affected objects,
start/end time, result, and conflict rows. Retrying an overridable conflict creates a
new linked attempt and leaves the original event read-only. This gives Nexus the same
audit shape as a Windchill-style Event Manager without coupling Creo to the database.

## Implementation status

1. Unified multi-object conflict dialog and typed conflict payloads: implemented.
2. Immediate edit rules for checkout, lifecycle, owner, permission, and workspace: implemented.
3. Check-in preflight for managed dependency sets: implemented.
4. Workspace baseline and out-of-date blocking: implemented.
5. Automated update/compare, family-table ghost resolution, and persistent PDM event retry history: pending.

## PTC references

- [Managing Conflicts](https://support.ptc.com/help/windchill/cloud/r12.0.2.0/en/Windchill_Help_Center/CADxWspActionsMenuConflictManage.html)
- [About Revise and Checkout](https://support.ptc.com/help/creo/creo_pma/r12/usascii/datamanagement/About_Revise_and_Checkout.html)
- [Checking In Objects to Windchill](https://support.ptc.com/help/windchill/plus/r13.1.2.0/en/Windchill_Help_Center/cadx/CADxFileMenuObjCheckin.html)
- [Managing Incomplete Dependent Objects](https://support.ptc.com/help/windchill/r13.1.2.0/en/Windchill_Help_Center/ProEWCInteg/ProEWCIntegAHIncompDependManage.html)
- [Synchronizing Your Workspace](https://support.ptc.com/help/windchill/r13.1.2.0/en/Windchill_Help_Center/cadx/CADxToolsMenuWspSync1.html)
- [Object Status Overview](https://support.ptc.com/help/windchill/plus/r13.1.2.0/en/Windchill_Help_Center/cadx/CADxComActivObjStatusAbout.html)
