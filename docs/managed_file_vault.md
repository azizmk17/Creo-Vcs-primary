# Nexus Managed Files

## Purpose

The BOM item remains the PLM object. Files are managed content attached to an exact
Nexus object iteration such as `D.5`. Nexus does not treat a Creo disk iteration such
as `cover.prt.17` as the same thing as object iteration `D.5`.

The Managed Files tab shows four content concepts together:

- Native CAD: the `.prt.N` or `.asm.N` file captured by a commit.
- Drawing: the `.drw.N` file captured by the same object iteration.
- Generated output: PDF and STEP files produced or attached during a commit.
- Document: manually attached supporting content with independent document versions.

## Version Meanings

| Value | Meaning |
|---|---|
| `D.5` | Nexus BOM object revision D, iteration 5 |
| `part.prt.17` | Physical Creo file iteration 17 |
| Document `v3` | Third version of a managed supporting document |

These counters are intentionally independent. The exact relationships are stored in
`bom_iteration_files`.

## Storage

Existing files remain at their existing paths and keep `storage_scheme = legacy`.
They are not moved, renamed, or deleted by migration 27.

New managed document versions and newly captured Creo content use:

```text
<project-family-working-directory>/.nexus/vault/blobs/
  <first-two-sha-characters>/
    <sha256>/
      <original-safe-filename>
```

The project-family directory is used so all project versions can resolve the same
immutable content. Database records store relative paths; no absolute path is added
to the schema.

Removing a document or version is a soft-delete operation. The content remains on
disk because a released iteration, baseline, issue, or commit may still reference it.

## Commit Workflow

1. The user checks out a BOM item and edits its Creo files.
2. The user commits the change. There is no standalone Check In action.
3. During merge, Nexus updates all native CAD and drawing rows for the BOM object.
4. Nexus performs one check-in and creates the next object iteration.
5. Nexus captures native CAD, drawing, active documents, PDF, and STEP references in
   the new iteration manifest.
6. Each captured physical file records its name, Creo iteration, hash, size, source,
   lifecycle state, commit, and storage scheme.

Updating all CAD/drawing rows before check-in is important. It prevents an object
iteration from binding the new part file to the previous drawing file.

## Release Workflow

Before releasing a BOM revision, Nexus captures the current iteration manifest. The
revision release then marks that manifest Released. Released object iterations and
their file bindings are immutable.

A later modification requires a new BOM revision. Old released manifests continue to
resolve their original files.

## Document Workflow

- **Attach Document** creates document version 1 and stores it as managed content.
- **Add Document Version** stores another immutable version.
- **Use in Working Iteration** changes the selected working document version and
  requires checkout ownership.
- **Approve Document Version** marks the document version approved/released.
- **Obsolete** hides the document from current work without deleting physical data.

The selected document version becomes part of the exact BOM iteration when the BOM
item is committed or released.

## Compatibility

- Existing `part_files` and `part_file_versions` IDs remain valid.
- Existing baseline, package export, issue traceability, and commit links continue to
  use `PartFileService`, which resolves both legacy and managed storage.
- Existing working directories and Creo files are unchanged.
- Migration 27 is additive: it adds metadata columns and the iteration manifest table.
- Old iteration rows without manifests are displayed as legacy snapshots. Nexus can
  show their captured filenames, but it does not claim a verified hash if one was
  never recorded.

## Health States

- `Verified`: Nexus stored the content and calculated its SHA-256 hash.
- `Available`: a legacy/working file resolves, but has not been copied into managed
  storage for that historical iteration.
- `Unknown`: historical metadata exists without a verified physical-content record.
- `Missing`: the expected path cannot currently be resolved.
