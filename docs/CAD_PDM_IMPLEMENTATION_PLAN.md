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
- A rejected CAD submission is resubmitted through a new Creo Check In, creating a new logical submission and fresh review while keeping the rejected record and reason in history.

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
- **Step 2 complete (snapshot and approval journal):** migration 45 records immutable, content-addressed file blobs and hash-checked manifest generations. Creo relationship metadata is sealed into a matching generation and checked against its pending record and file fingerprint at approval. Source CAD baselines are revalidated. Migration 46 adds a durable approval journal keyed by project, submission, and snapshot hash. It persists exact version/destination plans before copies, performs hash-verified atomic file staging, records monotonic approval phases, and makes commit status/signature writes idempotent so retries reuse the same approval ID and merge ID. Pending submissions created before migration 45 must be re-staged before approval because their original submitted bytes cannot be proven.
- **Step 2 complete:** approval publication applies reviewed CAD structure, approved commit rows, EBOM CAD/drawing filename pointers, idempotent approval and Item check-in signatures, managed CAD iterations/latest-file pointers, CAD checkout history/release, associated EBOM Item lock release/check-in logs/iterations, the project invalidation event, and the RECORDS_FINALIZED journal phase in one SQLite transaction. A failure rolls these records back together. A durable, machine-scoped workspace-release queue is committed alongside them and drained after commit and during application startup. Fault coverage verifies an Item check-in failure rolls back the CAD iteration and approval records, then retry commits each once; queue tests cover machine ownership and cleanup retry.
- Files are staged immutably and hash-verified before publication. Filesystem activation and SQLite cannot share a native transaction, so the persisted approval plan and workspace queue provide idempotent recovery for that boundary.
- **Step 3 complete:** CAD checkout, associated Item locks, and related drawing checkout records share one SQLite transaction; a conflict rolls the entire checkout back. Workspace scans and staging block stale baselines; frozen snapshots retain retrieved and approved hashes and approval revalidates exact base revision/iteration/hash when legacy server hashes exist. Explicit reconciliation archives every local iteration with hashes, refreshes the latest approved file as read-only, and updates the baseline. Undoing an Item checkout is blocked while associated CAD is active; closing CAD releases only clean CAD-origin Item locks and retains explicit or changed Item work. The focused checkout/workspace/snapshot/preflight suite passes (37 tests).
- **Step 4 implementation closed; native validation handed off:** synchronous Creo check-in captures metadata for selected existing as well as new CAD files. A loaded assembly is marked complete only when read from the selected workspace; complete snapshots can add, update, or remove CAD occurrence links after approval, while partial payloads cannot imply removals. Removed CAD links detach EBOM/build references before deleting only obsolete CAD-member rows; matching rows retain their identity. The J-Link review calls Nexus for the relationship diff and displays each unchanged dependency's approved revision, iteration, and full available SHA-256. The reviewed baseline is carried into staging and revalidated there and at approval, closing the review-to-submit race. Focused tests cover review output, review/stage and stage/approval dependency drift, removal, staged-snapshot replacement, and authored EBOM link integrity. The current synchronous J-Link build succeeds against Creo M020 `pfc.jar` with JDK 7u80, and the generated JAR contains the entry point and mutation guards. Creo M020's trail confirms NexusPDM starts via registry; interactive snapshot/check-in behavior is unverified and is left for user validation.
- **Step 5 implementation closed; runtime validation handed off:** the merge query carries submitter and designer IDs into approval preflight, and publication rechecks the acting approver and active project. Interrupted retries stay with their original approver; administrator approval requires a reason persisted in the journal and written to a separate signature. Migration 48 adds append-only approval phase events, including a legacy-state backfill. Submitters can withdraw Pending or Validated submissions before approval; the complete group changes state atomically, audit events are recorded, files and CAD/Item checkouts are retained, and approved or inconsistent groups cannot be withdrawn. A different authorized reviewer can reject a complete Validated group with a required reason; files/checkouts remain intact and any approval journal becomes terminal. Resubmission uses a fresh Creo Check In: appending is restricted to same-user Pending submissions, so a rejected submission cannot be reused; a new batch gets a new logical ID and immutable snapshot, and Creo stages new structure metadata against that ID. Migration 49 records lifecycle transitions. The focused bridge, CAD structure, submission lifecycle, and merge-preflight suites pass (49 tests). The running Nexus-to-Creo approval and rejection/resubmission flow was not exercised; user validation is pending.
- **Step 6 shared-folder operations phase implemented:** Creo continues to call the versioned, token-authenticated `/api/v1` bridge; the Java client does not access SQLite directly, and the bridge delegates mutations to existing PDM services. The Diagnostics page now has a PDM Operations view for SQLite `quick_check`, a brief non-writing lock reservation, active-project root/commit/snapshot directory availability and temporary write/delete probes, incomplete approval journals with original-reviewer recovery guidance, and machine-scoped workspace cleanup queues. Local cleanup retry requires confirmation and is restricted to the active project and this machine. Focused service/UI checks pass, including a live scan of the isolated project clone. Remaining Step 6 work: introduce a remote file-store/transport adapter and host the same versioned operations behind a central authenticated service when the deployment moves off the shared-folder database; that migration is intentionally not part of the current deployment phase.
