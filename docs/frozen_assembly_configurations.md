# Nexus Assembly Configurations

## Purpose

A configuration is a named, versioned engineering structure for a prototype, 3D-print
job, manufacturing trial, validation build, or customer variant. It is separate from
the official project BOM and does not change BOM relationships, object revisions,
commits, locks, baselines, or project versions.

Every occurrence selects one exact checked-in object iteration such as `B.3` and keeps
its quantity and parent relationship. Repeated parts are stored as separate
occurrences.

## Lifecycle

Each configuration has its own version sequence:

```text
Prototype Alpha v1 Draft
Prototype Alpha v1 Frozen
Prototype Alpha v2 Draft
Prototype Alpha v2 Frozen
```

- **Draft** is editable and cannot be built.
- **Frozen** is immutable and can be built repeatedly.
- **Create New Version** copies a Frozen version into the next Draft version.
- Freezing never modifies or replaces an earlier Frozen version.

Configuration versions are not part revisions. A configuration `v2` can still contain
part `A.4`, assembly `D.2`, and drawing `B.7`.

## Create and Customize

1. Select a root assembly in the BOM and click **Create Configuration**.
2. Select the checked-in root assembly iteration used as the starting structure.
3. Enter a name, purpose, and description.
4. Use the searchable **Project BOM Components** pane to find components.
5. Drag one or more components onto an assembly in the **Configuration BOM**, or use
   **Add to Selected Parent**.
6. Remove occurrences, change an occurrence's exact version, or change its quantity.
7. Choose **Save Draft** or **Save and Freeze**.

Adding an assembly imports the exact recursive structure bound to its selected
iteration. Adding a part creates one leaf occurrence. Nexus rejects cross-project
items, disconnected branches, parts used as parents, and circular structures.

Saving a Draft writes database data only. Missing physical CAD files do not prevent a
Draft from being saved.

## Edit and Version

The configuration manager lists every version and its state.

- **Edit Draft** opens the same BOM/configuration editor.
- **Freeze** validates files and makes that version immutable.
- **Create New Version** is available only for a Frozen version and creates the next
  Draft with the same structure and exact iteration selections.
- A new version starts only from the latest version in that configuration series.
- Frozen versions cannot be edited or deleted.
- Draft versions cannot be built.

Changing an occurrence version changes only that selected occurrence. Its existing
custom configuration children remain until the user adds or removes them explicitly.
Changing the root iteration from the selector replaces the whole Draft with the exact
structure captured by that root iteration after confirmation.

## Data-Only Freeze

Freeze validates every selected native Creo file and optional drawing in the project
working directory. It stores:

- Relative source path
- Exact Creo filename, including `.prt.N`, `.asm.N`, or `.drw.N`
- SHA-256
- BOM object, revision, and iteration IDs
- Occurrence path, parent, order, and quantity
- Freezing user and time

Freeze creates no physical workspace, hidden file archive, or copied CAD file. If two
different files require the same flat Creo workspace filename, Freeze is rejected.

## Build Configuration

Build is available only for a Frozen version.

1. Select **Build Configuration**.
2. Select a parent destination directory.
3. Nexus verifies each source file against its frozen SHA-256.
4. Nexus creates a uniquely named workspace.
5. Nexus copies the selected exact CAD files and writes
   `nexus_configuration_manifest.json`.

Existing directories are never cleared or overwritten. Missing files or hash changes
stop the build. Cancelling removes only the partially created workspace.

Because configuration creation and Freeze are data-only, the exact historical files
must remain available in the project working directory. Retention independent of the
working directory requires a separate vault policy.

## Creo Boundary

Nexus controls the configuration manifest and the exact file set. A native Creo
`.asm.N` binary still contains its own component references. Adding or removing a
configuration occurrence does not rewrite that binary assembly file. A customized
workspace therefore provides the selected exact files and manifest; automatic creation
of a different native Creo assembly requires a future Creo Toolkit, J-Link, or supported
Creo automation integration.

Opening the copied root assembly reproduces its native references. The manifest remains
the authority for custom additions and removals until such an integration is present.

## Integrity Rules

- The root must be a checked-in assembly iteration.
- Every occurrence must select an iteration belonging to its BOM object and project.
- Only assemblies can contain configuration children.
- A root occurrence cannot be removed or replaced with another BOM object.
- Circular and disconnected structures are rejected.
- Parts and assemblies require native files when Freeze is requested.
- Frozen versions are immutable.
- Only Frozen versions can Build or create a new configuration version.
- Deleting a Draft never deletes BOM data or built workspaces.

## Database

Migration 24 introduced `assembly_configurations` and
`assembly_configuration_members`. Migration 25 adds:

- Stable configuration series key and display name
- Configuration version number
- Draft/Frozen state
- Based-on version link
- Draft update time
- Frozen user and time

Existing configurations are preserved as `Frozen v1`. Migration 25 does not modify BOM
objects, object iterations, exact child bindings, files, commits, locks, snapshots,
baselines, or project directories.

The members table stores the version-specific occurrence tree and exact object
iteration selections. Source paths and hashes are populated only when that
configuration version is Frozen.
