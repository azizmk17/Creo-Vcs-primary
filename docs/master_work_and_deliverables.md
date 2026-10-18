# Master work and deliverables — Nexus 4.0.7

Each project's working directory contains a `master` folder. Checking in a
native CAD submission leaves it Pending; it does not automatically publish it.
A **master or admin** can open the Pending submission and choose **Push to Master**
to publish its entire frozen native snapshot as **Under development**, before
validation or integration. Use **Open Master** in the BOM Files actions to inspect
it. Designers and checkers cannot perform this publication.

A development push records Integrated status, removes the group from the Pending
queue, and retains its CAD checkouts and frozen submission files. It records the publishing user and source commit in the master
manifest and emits a project event. It does not approve the submission, close
issues, apply the pending assembly structure, or publish PDF/STEP attachments.
The existing validation and approval actions remain available afterward.

A published Integrated snapshot cannot be extended or replaced in place. To
submit more changes, create a new commit. Its master files
and source history remain available until the next authorized push.

Master contains native `.prt`, `.asm`, and `.drw` files with stable filenames.
Its `manifest.json` records the originating submission, original filename, and
SHA-256 digest. On first publication, existing native files in the project
working directory seed the dependencies; their provenance remains unknown.
Existing submissions are not retroactively republished during app startup.

Approval continues to preserve its existing native snapshots and deliverable
history. Approving an older submission does not overwrite the newer development
files in `master`. Releases, version-family storage, and frozen baselines retain
their existing behavior; this change does not migrate existing release paths.

The BOM Status shows **Under development** or **Validated** for the native
work actually pushed to master. Newer unpushed submissions do not change it.
Validation and export availability are independent. Rejected or withdrawn
pushed work remains visible in master as under development until superseded.

PDF and STEP badges use source-submission provenance:

| State | Meaning |
| --- | --- |
| Current / green | Export belongs to the current validated or approved submission |
| Previous validated / amber | Export belongs to an older validated or approved submission |
| Unverified / blue | File exists, but its source is unknown or not validated |
| Missing / not attached | No available export |

Hover over each badge for its source and current native submission identifiers.
BOM exports include the readable status and provenance details. A newer draft
export does not hide an available validated export. Filenames and timestamps
alone do not establish validated provenance. Manual acknowledgements retain
their explicit user-confirmed status without creating validated provenance.

Master publication serializes writers with `.publish.lock`, stages files, and
restores overwritten files if publication raises an error. Readers should wait
for publication to finish before opening an assembly. After a process crash,
an administrator may need to inspect and remove a stale lock before retrying.


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


## Push without validation

Push to Master for a Pending commit displays a warning with Cancel selected by
default. Confirming publishes the frozen native files and records the whole
commit as Integrated, with no checker or validation granted. The commit leaves
the pending queue and remains in history; the BOM stays Under development.
Validated PDF/STEP files retain their previous provenance. A failed status update
restores the master files and manifest and leaves the commit Pending.

Use **Review for Validation** in the integrated commit's history details to
validate it later. Validation is a separate action; it returns the group to the
Validated approval queue.


## Existing database compatibility

Upgrading to 4.0.7 preserves the original PDF/STEP status rules and stored manual
acknowledgements for parts that have not been explicitly pushed through the new
master workflow. The upgrade does not require re-marking existing files as up
to date and does not rewrite acknowledgements or fabricate source-version links.
Files still missing on disk or already needing review retain those conditions.

The newer source-version indicators apply per part after an explicit master
push; merely seeding another part into master does not change legacy indicators.
Manual acknowledgements against the current master remain supported.
