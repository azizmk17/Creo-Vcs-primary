# Master work and deliverables — Nexus 5.0.0

The configured project working directory is the Master. Native Creo files are
numbered iterations in this root, such as `part.prt.1` and `part.prt.2`.
**Open Master** opens that root. The app stores publication metadata and the
publication lock under `.nexus`; it does not create another native Master copy.

When a project is opened, Nexus converts a recognized old `master` folder. It
reuses matching validated root files and preserves validated branch files that
were only in the old folder as new numbered root iterations. Unvalidated native
copies in the old folder are discarded. Referenced PDF/STEP deliverables are
preserved in `deliverables/` and their live database paths are updated. Existing
snapshots and frozen releases retain their own storage and history. Unknown
folders, missing files, unsafe links, and conflicting files stop conversion.
The old folder is removed only after the conversion is recorded.

Upgrade all clients before opening the shared project in this version. Older
clients use a different publication lock and folder layout and must not access
it concurrently. Back up the database and project before the company rollout.
A stopped conversion can be retried after resolving its reported error.

Shared access depends on both the Windows share and filesystem permissions.
The project team needs the appropriate read, create, modify and delete rights
on the project root and its `.nexus`, `commits`, and `deliverables` folders.
New shared publication files, snapshots and approval files inherit their
destination folder's permissions. The app does not grant access to accounts or
change an existing folder's ACL. If an older `.nexus` folder or file is private,
an administrator must repair its inheritance for the approved project group.
Verify with two different company accounts on the actual network share.

New work can still be pushed without validation by a Master/Admin. It creates a
root iteration and remains Under development; validated exports retain their
provenance. Approval and branch merges use root iterations as well. PDF and STEP
acknowledgements and version history are not reset by this conversion.

## Check-in conflicts

If a staged native file already exists in a Pending submission, check-in opens
**Replace**, **Skip**, and **Cancel** choices. The decision applies to all
conflicting files in that check-in. Replace is disabled for another user's
submission or a snapshot already published to master. Skip checks in remaining
files and keeps conflicting files and their form details staged for review.
Cancel leaves the form unchanged. If every file is skipped, no new submission
is created.


## Independent EBOM undo checkout

Undo Item Checkout in EBOM restores the selected Item's checked-in attributes
and structure and releases only its Item checkout. Associated CAD checkouts,
other Items, and local workspaces remain active, regardless of their owners.
No iteration is created. The selected Item's existing ownership and Master/Admin
permissions still apply. Legacy shared-file undo and Item check-in keep their
existing rules.


## Existing database compatibility

Upgrading to 4.0.8 preserves the original PDF/STEP status rules and stored manual
acknowledgements for parts that have not been explicitly pushed through the new
master workflow. The upgrade does not require re-marking existing files as up
to date and does not rewrite acknowledgements or fabricate source-version links.
Files still missing on disk or already needing review retain those conditions.

The newer source-version indicators apply per part after an explicit master
push; merely seeding another part into master does not change legacy indicators.
Manual acknowledgements against the current master remain supported.
