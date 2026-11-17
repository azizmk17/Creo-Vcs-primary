# Reconcile manually renamed Creo files

Use this operation after the Creo user has renamed the model and, if applicable,
its drawing in the project root. The user must update and save affected Creo
assemblies and drawings in Creo; Nexus cannot verify or repair their internal
references.

1. Open the product and version in Nexus and find the PRT or ASM CAD Document.
2. Right-click it and select **Reconcile renamed CAD files**.
3. Enter the new model name and, for each related managed drawing, either enter
   its new name or leave its current name unchanged. If only a drawing was
   renamed, leave the model name unchanged.
4. Nexus checks the renamed files and reports any conflict. When successful,
   the CAD Document IDs and Item associations are retained. The rename appears
   in the Item timeline and the current Master and diagnostic readers resolve
   recorded files under their new names. Historic snapshots and commit records
   retain their original names.

The operation supports files whose renamed bytes match the recorded Master or
current CAD iteration hash. If Creo changed the file contents, if a recorded
hash or iteration is missing, or if a validated Master file is unavailable,
Nexus refuses the reconciliation instead of carrying old validation onto new
bytes. Changed CAD content must be handled through the controlled CAD workflow.

For safety, the operation blocks while an affected CAD document or a CAD
document associated with its Items is checked out, a related Item is checked
out, or an active Nexus branch contains the affected CAD or a related Item.
Unrelated checkouts and branches do not block it. A paused branch does not
block reconciliation; its frozen snapshot retains the old filename, so review
and reconcile the branch contents before resuming or merging it. The operation
also blocks on project-wide pending or validated commits, or another project
sharing the directory and the same CAD filename. A read-only project version
cannot be reconciled. Complete or resolve those conflicts first.
The operation does not delete old files from users' local CAD workspaces; users
should retrieve the newly named CAD Document into their workspace after the
reconciliation and review any old local copies.

This feature adds database migration 54. Update all Nexus clients that use the
shared database before using it. Nexus stores rename mappings in SQLite and
applies them when reading the Master manifest; it does not rewrite the original
manifest as part of reconciliation.
