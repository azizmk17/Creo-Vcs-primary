# Supplier-managed CAD packages

A supplier-managed CAD package is a normal physical, deliverable BOM item whose
internal Creo dependencies are intentionally outside individual BOM and integrity
control. The owning assembly keeps its normal native CAD, drawing, PDF, STEP,
revision, release, and integrity behavior.

Internal package files are stored in `bom_cad_dependencies`. They are ownership
records, not BOM objects, and therefore do not receive individual drawing, export,
lifecycle, or integrity requirements.

## Authoring workflow

1. Edit the owning physical assembly.
2. Set **CAD Control** to **SUPPLIER PACKAGE**.
3. Open **Diagnostics** and run a scan.
4. In **Orphan Files** or **Working Dir Check**, select the supplier's internal Creo
   files. Multi-selection and Select All can be used for large packages.
5. Choose **Assign selected to supplier package** and select the owning assembly.

The files disappear from orphan/unexpected results and appear in the **Supplier
Packages** tab. Use **Unassign selected package files** to correct ownership.

If an internal file later needs individual control, create its normal BOM item. The
matching ownership record is removed automatically, and the item receives standard
integrity, commit, and release behavior.

## Rules

- Only a BOM item with `cad_control_mode = SUPPLIER_PACKAGE` can own dependencies.
- A dependency base filename has one owner within a project.
- Dependency ownership is copied and remapped when creating a new project version.
- Staging a dependency for an individual commit is blocked with a message directing
  the user to commit the owning assembly instead.
- A package cannot be changed back to `CONTROLLED` or deleted until its dependency
  ownership records are removed.
