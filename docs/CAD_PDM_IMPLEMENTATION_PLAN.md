# CAD PDM Implementation Plan

## Agreed workflow

- Deploy against the shared folder for now, with a service boundary that can move behind a central server later.
- A Creo submission stays private and pending until an authorized project approver approves it.
- Keep the submitter's CAD and associated EBOM Item checkouts while a submission is pending or rejected.
- Freeze each submitted file and its Creo relationship metadata. Replacing a pending submission replaces the complete snapshot and invalidates its review.
- Publish a submission all-or-nothing. A failed or incomplete submission must not change approved CAD files or relationships.
- Require new or changed dependencies in the submission. An unchanged dependency can be referenced at its exact approved iteration. A drawing submission includes its related model.
- Allow explicitly classified CAD-only reference/supporting documents; distinguish them from missing Item associations.
- Continue Locally drafts and work based on an outdated iteration cannot be submitted. Reconciliation into a current checked-out copy is explicit and preserves the draft.
- CAD checkout and associated Item checkout succeed or fail together. CAD structure approval does not silently rewrite the authored EBOM.
- Project approvers approve other users' submissions. Administrator overrides require an audited reason.
- The author may withdraw before approval; withdrawal invalidates review, preserves files, and retains checkouts. Undo Checkout is separate.

## Delivery sequence

### 1. Submission preflight and identity

- Resolve every selected file row to its logical submission ID and project.
- Require the complete submission and all pending source files before copying or changing status.
- Validate staged CAD relationship metadata against the current project state before merge.
- Apply structure metadata using the logical submission ID, not a per-file row ID.
- Acceptance: partial selection, missing files, stale/invalid relationships, and wrong-project rows fail before merge side effects; a valid complete submission passes preflight.

### 2. Frozen, recoverable submission snapshots

- Store immutable file bytes, SHA-256, base CAD iteration/hash, metadata snapshot, and a unique batch ID together.
- Make append/replace operations update the full snapshot atomically and invalidate prior review.
- Add explicit submission states and idempotent retry/recovery for interrupted upload or approval.
- Acceptance: interruption at each state can be retried or recovered without duplicate iterations, lost source files, or partial publication.

### 3. Coordinated checkout and workspace baselines

- Make CAD plus associated Item checkout one atomic domain operation, with shared Item lock ownership across related CAD documents.
- Record exact retrieved revision, iteration, and hash. Revalidate at submission and approval.
- Keep outdated copies and Continue Locally drafts blocked; expose explicit reconciliation that preserves local work.
- Acceptance: competing checkouts, stale work, and checkout release are deterministic across users/workspaces.

### 4. Creo metadata and dependency review

- Capture synchronous J-Link metadata for new and existing models: assembly occurrences, removals, dependencies, drawing/model links, and CAD-only classification.
- Show a reviewed change/dependency plan. Incomplete scans cannot imply removals; unchanged dependencies reference exact approved iterations.
- Acceptance: approved CAD structure matches the submitted Creo snapshot; authored EBOM and variant decisions remain intact.

### 5. Approval and publication

- Enforce project Approver role and no self-approval; audit administrator overrides and every state transition.
- Recheck reviewer identity, snapshot hash, base iterations, locks, permissions, and dependencies at approval.
- Publish file versions, CAD structure, Item checkout outcomes, and audit records as one recoverable all-or-nothing operation.
- Support reject, withdraw, replace, and retry without changing approved data.
- Acceptance: a required validation or storage failure publishes none of the submission; success publishes all of it once.

### 6. Shared-folder operation and central-server migration

- Keep business rules behind service interfaces; use stable IDs, versioned requests, short database transactions, and explicit file-store operations.
- Add health/recovery visibility for shared-folder lock, database, and file-store failures. Later host the same operations behind a central authenticated API and move shared SQLite to a server database.
- Acceptance: clients cannot bypass checkout/approval rules, and the server migration does not change the submission workflow.

## Progress

- **Step 1 complete:** approval requires every validated row in a logical submission, verifies the frozen files, validates staged CAD relationships without applying them, and applies structure with the logical submission and project IDs. Focused checks cover incomplete groups, mixed states, missing files, and read-only structure validation.
- **Step 2 in progress:** migration 45 records immutable, content-addressed file blobs and hash-checked manifest generations. Creo relationship metadata is sealed into a matching generation and checked against its pending record and file fingerprint at approval. Source CAD baselines are revalidated. Migration 46 adds a durable approval journal keyed by project, submission, and snapshot hash. It persists exact version/destination plans before copies, performs hash-verified atomic file staging, records monotonic approval phases, and makes commit status/signature writes idempotent so retries reuse the same approval ID and merge ID. Pending submissions created before migration 45 must be re-staged before approval because their original submitted bytes cannot be proven.
- **Step 2 complete:** approval publication applies reviewed CAD structure, approved commit rows, EBOM CAD/drawing filename pointers, idempotent approval and Item check-in signatures, managed CAD iterations/latest-file pointers, CAD checkout history/release, associated EBOM Item lock release/check-in logs/iterations, the project invalidation event, and the RECORDS_FINALIZED journal phase in one SQLite transaction. A failure rolls these records back together. A durable, machine-scoped workspace-release queue is committed alongside them and drained after commit and during application startup. Fault coverage verifies an Item check-in failure rolls back the CAD iteration and approval records, then retry commits each once; queue tests cover machine ownership and cleanup retry.
- Files are staged immutably and hash-verified before publication. Filesystem activation and SQLite cannot share a native transaction, so the persisted approval plan and workspace queue provide idempotent recovery for that boundary.
- **Step 3 complete:** CAD checkout, associated Item locks, and related drawing checkout records share one SQLite transaction; a conflict rolls the entire checkout back. Workspace scans and staging block stale baselines; frozen snapshots retain retrieved and approved hashes and approval revalidates exact base revision/iteration/hash when legacy server hashes exist. Explicit reconciliation archives every local iteration with hashes, refreshes the latest approved file as read-only, and updates the baseline. Undoing an Item checkout is blocked while associated CAD is active; closing CAD releases only clean CAD-origin Item locks and retains explicit or changed Item work. The focused checkout/workspace/snapshot/preflight suite passes (37 tests).
- **Step 4 in progress:** synchronous Creo check-in captures metadata for selected existing as well as new CAD files. A loaded assembly is marked complete only when read from the selected workspace; complete snapshots can add, update, or remove CAD occurrence links after approval, while partial payloads cannot imply removals. Removed CAD links detach EBOM/build references before deleting only obsolete CAD-member rows; matching rows retain their identity. The J-Link review calls Nexus for the relationship diff and displays each unchanged dependency's approved revision, iteration, and full available SHA-256. The reviewed baseline is carried into staging and revalidated there and at approval, closing the review-to-submit race. Focused tests cover review output, review/stage and stage/approval dependency drift, removal, staged-snapshot replacement, and authored EBOM link integrity; J-Link source compiles against the installed Creo M020 `pfc.jar` with JDK 7u80. Remaining Step 4 work: verify the native snapshot path in an interactive Creo session and finish any compatibility fixes found there.
- **Step 5 in progress:** approval blocks submitter self-approval, binds an interrupted approval retry to its original approver, and writes an explicit signature when a system administrator uses the override path. Migration 48 adds append-only approval phase events, including a legacy-state backfill. A submitter can withdraw a Pending or Validated submission before approval; the complete group changes state atomically, audit events are recorded, frozen files and CAD/Item checkouts are retained, and approved or inconsistent groups cannot be withdrawn. A different authorized reviewer can reject a complete Validated group with a required reason; files/checkouts remain intact and any approval journal becomes terminal. Migration 49 records lifecycle transitions. Focused tests cover identity, phase ordering/retry, withdrawal/rejection ownership, group atomicity, audit reasons, and terminal states. Remaining: implement rejected-submission resubmission with a fresh immutable snapshot/review identity, exercise permissions through application entry points, and run the broader approval regression suite.
