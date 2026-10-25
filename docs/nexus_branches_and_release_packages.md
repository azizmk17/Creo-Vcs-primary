# Item Timeline, Nexus Branches, and Frozen Release Packages

This workflow is available in **Nexus 4.8.0**. Database migration **50** adds
the branch, snapshot, release-package, and item-event records. Migration **51**
adds per-user active-branch selections and commit-to-branch tracking. Both are
additive: they do not rewrite existing PDF, STEP, CAD, or deliverable status
values.

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

Choose **Freeze Release Package** to select the exact files, provide the
document/package revision (such as `A010`), and add a note. The package records
the CAD/project revision (such as `DV12`) separately and stores immutable,
content-verified copies of the selected files. It is a snapshot, not a
validation action and not an item lifecycle transition.

Use **Export Selected Release** to export an earlier package later. The ZIP
contains a manifest with both revision labels and the exact frozen filenames;
each payload uses readable folders such as `Native CAD`, `Drawings`, `PDF`, and
`STEP`, with the original filename. Subsequent
master edits do not alter an existing package. Content-addressed storage
deduplicates identical bytes between snapshots.

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

The branch gets a separate workspace under the project's `.nexus/branches`
folder for each user. Saving CAD changes there, checking in branch
parameters/quantities, pausing, and resuming do not change master. A paused
branch retains its workspace. Refreshing a workspace preserves the prior local
folder as a saved copy before materializing the latest branch snapshot.

From a Pending or Validated commit's details in **Commit Workspace**, choose an
active branch and click **Push to Branch**. The app verifies the sealed commit
files, copies them to that branch's separate workspace, and records a branch
snapshot. The commit leaves the review queue as **Branched**. **Push to Master**
remains a separate action and publishes to the project master directory.

Users with project commit permission can work in separate workspaces on an
active branch; only a master user can merge it. Review compares the branch's base, current
master, and branch head. Conflicts require an explicit choice. Creo geometry
does not receive automatic binary merging. New CAD filenames without an
existing Item association are blocked until they are associated through the
normal CAD/Item workflow.

## Merge and validation

Merge publishes selected native CAD and Item changes through the existing
master publication transaction. New master commits are **Integrated / Under
development**. Normal validation remains a separate action. The merge does not
change item lifecycle to Released and does not replace active validated PDF or
STEP exports. Document-only changes stay in the branch snapshot and can be
frozen as a release package; they are not used to silently overwrite master
deliverables.

Merges are rejected if the reviewed master changed, the branch head changed,
the item needs a new lifecycle revision, a master checkout is active, or the
native file gained another Item association after the branch was created.
Review again from a fresh branch after resolving these conditions.

The timeline loads item metadata and the master manifest; it does not scan or
hash the entire working directory when opening the tab. File hashing and
snapshot work run in a background task when creating, checking in, freezing,
exporting, or merging.
