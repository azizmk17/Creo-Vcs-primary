# Item Timeline, Nexus Branches, and Frozen Release Packages

This workflow began in **Nexus 4.8.0**. Migration **50** adds the branch,
snapshot, release-package, and item-event records. Migration **51** adds
per-user active-branch selections and commit-to-branch tracking. Migration
**53** adds named multi-item release packages and package revision metadata.
These migrations are additive: they do not rewrite existing PDF, STEP, CAD,
or deliverable status values.

## Revisions shown in the timeline

- **CAD / project revision** (for example, `DV12`) identifies the project
  version used for the native CAD state.
- **Document / package revision** (for example, `A010`) identifies the PDF,
  drawing, STEP, or other deliverable revision. It can advance independently
  from the CAD revision.
- An item lifecycle such as **Design**, **Released**, or **Obsolete** remains
  separate from both revision labels and from whether master contains validated
  native work.

The BOM's **Timeline & Branches** tab shows prior activity newest first. A
release is a point on the master timeline. A branch lane forks only at its
creation event; its line does not originate at a release marker. Historical
release activity is not retroactively treated as a frozen package.
The graph omits checkout and undo-checkout noise. A check-in linked to the same
Item iteration as a commit is represented by the commit; standalone check-ins
remain visible. Timestamps are displayed in the computer's local timezone,
including legacy records stored as either local or UTC time and newer UTC
release events.

## Freeze and export a release package

Choose **Release Package** to create a named package, such as **3D Printing ·
Housing Set**, and select one or more BOM items. Set the document revision
(such as `A010`) for each item, add the shared release note, then select the
exact files to freeze. The package records each item's CAD/project revision
(such as `DV12`) separately from its document revision. It stores
content-verified immutable files. Releasing is a snapshot action; it does not
validate the items or change their lifecycle state.

To add another item later, select that item's row, choose **Release Package**,
select the existing package, search for and add the new item. The app creates the next
package revision. It carries forward every existing member and its exact
released files and revisions, then adds the newly selected item's snapshot.
The earlier package revision remains available unchanged. The timeline records
the new package revision for every member, including the added item, so a user
can see which items belonged to each revision.

Use **Export Package Revision** to export the exact revision selected in the
timeline. The ZIP manifest lists all member items, their CAD and document
revisions, and exact frozen filenames. Multi-item exports use readable
per-item folders, for example `Parts/<part name>/Native CAD` and
`Parts/<part name>/PDF`. Subsequent master edits and later package revisions do
not alter an earlier export. Content-addressed storage deduplicates identical
bytes between snapshots.

## Work on a Nexus Branch

Create a branch from an item in the BOM. It records master as it existed at the
branch's creation time. The branch is independent of later master changes and
of frozen release packages. Depending on the choice during branch creation,
the captured scope can include the selected assembly's children and referenced
files. Items that share a native CAD association are kept together.

Select one or more BOM Items and use **Active Branch** to set your working
branch for them. A branch appears in the selector only when it contains every
selected Item. The active branch is saved per user, so each teammate can choose
their own branch for the same Item. The BOM's **Active Branch** column shows
your selection; **Master** means no personal branch is active.
The branch column is always visible immediately after the row number. It uses
an icon and a green **Master**, purple **Nexus branch**, or amber **Paused**
badge. Hover over a shortened branch name to read its full name and state.

The app manages a separate workspace under the project's `.nexus/branches`
folder for each user. Continue editing CAD in your own working directory;
the app copies committed CAD and documents into its branch workspace when you
push to that branch. Branch parameter changes, pausing, and resuming do not
change master. A paused branch retains its workspace. Refreshing a workspace
preserves the prior folder before materializing the latest branch snapshot.

From a Pending or Validated commit's details in **Commit Workspace**, choose an
active branch and click **Push to Branch**. The app verifies the sealed commit
files, copies them to that branch's separate workspace, and records a branch
snapshot. The commit leaves the review queue as **Branched**. **Push to Master**
remains a separate action and publishes to the project master directory.

### Access PDFs, STEP files, and other branch documents

Commit PDFs, STEP exports, and other associated documents together with the CAD
files. **Push to Branch** freezes those attachments in the branch snapshot and
copies them into the workspace managed by the app. Users can stage their source
files from any working directory; branch checkout does not open a folder.

In the Item's **Timeline & Branches** tab, select a branch and click **View Branch
Files** to see its latest files. To retrieve an earlier commit's files, select its
timeline event and click **View Commit Files**. The list shows the original
filename, document revision, source commit, and availability. Select a PDF, STEP,
or CAD file and choose **Open** or **Save a Copy**. Opening uses a verified copy,
so it does not edit the stored branch snapshot.

The file viewer also works for paused and merged branches after workspace
cleanup. New snapshots retain their documents independently of the commit
attachment folder. For commits pushed by older app versions, the viewer reads
the original stored commit attachments where they are still available; existing
history and frozen releases are not rewritten.

If Windows or shared-folder permissions block workspace archiving after a
merge, the app reports **Merge complete — workspace cleanup pending**. The
branch stays closed and the BOM returns to Master. Unlocked workspace contents
are moved into the optional archive; any locked files are preserved in their
original location. This fallback moves files without making another large
copy. Close applications using those branch files or folders, select the merged
branch in **Timeline & Branches**, and click **Retry Workspace Cleanup**. Retrying
only completes cleanup; it does not repeat the merge or create another commit.

Users with project commit permission can work in separate workspaces on an
active branch; only a master user can merge it. Review compares the branch's base, current
master, and branch head. Conflicts require an explicit choice. Creo geometry
does not receive automatic binary merging. New CAD filenames without an
existing Item association are blocked until they are associated through the
normal CAD/Item workflow.

## Merge and validation

Merge publishes selected native CAD and Item changes through the existing
master publication transaction. An unchanged **Validated** branch commit keeps
its validation when merged: the BOM development state becomes **Validated**,
and the completed Master commit is recorded as approved. Pending branch work,
edits after validation, or a combination of Master and branch changes that was
not validated becomes **Integrated / Under development**. Validation is evaluated
separately for each affected Item. Merging does not set Item Status to Released.

Selected PDF, STEP, and other documents are published under
`deliverables/<type>/<item>/` and registered as active document versions in
**Managed Files**, including when the Item previously had no PDF or STEP record.
The app updates the development state and PDF/STEP badges immediately after
merge. Exports from the same validated native state are current; an older
validated export stays identified as belonging to earlier native work. New
unvalidated outputs do not become validated just because they were merged.

Native CAD, document publication, document bindings, and validation status are
updated together. If a publication or database operation fails, the previous
Master files and database versions are restored. Previously frozen release
packages keep their exact original files.

Merges are rejected if the reviewed master changed, the branch head changed,
the item needs a new lifecycle revision, a master checkout is active, or the
native file gained another Item association after the branch was created.
Review again from a fresh branch after resolving these conditions.

The timeline loads item metadata and the master manifest; it does not scan or
hash the entire working directory when opening the tab. File hashing and
snapshot work run in a background task when creating, checking in, freezing,
exporting, or merging.

## Project timeline and all releases

Choose **Project Timeline & Releases** in an item's timeline tab to open the
project-wide view. Time runs from left to right. The Master lane contains every
frozen release package revision and a daily summary of standalone Master
check-ins. Commits are intentionally omitted to keep the graph readable on
projects with large commit histories. Each Nexus branch has its own lane: the line forks
at branch creation, continues through its branch activity, and returns to
Master when merged. Active, paused, and merged branches use distinct line
styles and colors.

Use **+** and **−** to zoom, or **Reset zoom** to return to the normal scale.
Select a release card to open its exact package contents in the lower panel.
Each BOM item shows its CAD and document revisions, with the frozen CAD,
drawing, PDF, STEP, and other files nested underneath. Use **Export Selected
Package Revision** to export that exact revision. Filters can show only
releases, only branches, or Master check-ins when the full timeline is busy.
