# Engineering Issue Management

## Purpose

The Issue Management subsystem is a BOM-integrated engineering governance layer. It keeps one
engineering issue traceable through affected parts and assemblies, commits, validation decisions,
merges, project revisions, snapshots, and release gates.

## Architecture

The subsystem follows the existing application layers:

- `core/models/issue_model.py`: workflow constants, transition rules, and issue model.
- `core/repositories/issue_repository.py`: normalized SQLite persistence and append-only audit data.
- `core/services/issue_service.py`: workflow validation, dependency propagation, diagnostic sync,
  risk aggregation, commit validation, attachments, and release gates.
- `pages/issue_page.py`: Issue Center, filters, metrics, issue detail, comments, attachments, and audit.
- `pages/bom_page.py`: inline issue counts, inherited assembly risk, health score, and Part Details tab.
- `pages/commit_page.py`: resolved-issue claims and validator confirmation/rejection.

Repository calls use short-lived SQLite connections, so diagnostic/background workers do not share
connections with the GUI thread.

## Data Model

- `issues`: issue identity, workflow, priority, category, assignment, project, due date, and archive state.
- `issue_parts`: many-to-many affected BOM parts. Links are carried forward when a project revision is created.
- `issue_comments`: immutable engineering discussion entries.
- `issue_commit_links`: resolution claims and their validation decision.
- `issue_attachments`: files stored below `<working_dir>/.creo_vcs/issues/<issue_number>/`.
- `issue_history`: append-only field, workflow, comment, attachment, and validation audit events.
- `issue_notifications`: assignment and lifecycle notifications.

Issues are never deleted. Archive operations preserve the complete audit trail.

## Workflow

Valid lifecycle:

`Open -> In Progress -> Ready For Validation -> Closed`

Validation rejection returns an issue to `In Progress`. Closed issues may be reopened to `Open`.
Linking an issue to a commit moves it to `Ready For Validation`. During commit validation, each
claimed issue is independently confirmed or rejected.

## BOM And Risk Propagation

The BOM tree keeps its existing columns. Active counts are painted inline beside the part name as
red `!N` indicators. Resolved-only parts receive a green state marker. Parent assemblies inherit
the distinct set of active and critical issues from all descendants, avoiding duplicate counts when
one issue affects multiple children.

Release of a part, assembly, or file version is blocked when that node or any descendant has an
unresolved Critical issue. Merge operations apply the same dependency-aware gate.

## Diagnostics And Snapshots

Full BOM diagnostic scans synchronize missing/outdated CAD, drawing, PDF, and STEP findings into
system-generated Validation issues. Repeated scans reuse the same issue; resolved findings close
automatically and reopen if detected again.

Snapshots store the current issue summary and issue-state rows alongside file metadata. Snapshot
details expose historical open, closed, and critical counts.

## Scaling Strategy

- Indexed project/status, priority, assignment, due-date, part-link, commit-link, and history queries.
- One aggregate issue-summary query per BOM refresh, followed by in-memory set propagation.
- Existing incremental BOM rendering remains unchanged.
- Diagnostics continue to run in the existing background worker and use independent DB connections.
- Issue Center queries are project-scoped and filter in SQL.

## Extension Points

- Geometry-linked issues can add geometry references keyed by issue and CAD face fingerprint.
- Screenshot annotation can extend `issue_attachments` with annotation JSON.
- Creo Toolkit events can call `sync_validation_findings`.
- Manufacturing and QA findings can use additional `source_type` values.
- Risk prediction and AI summaries can consume `issue_history`, validation results, and assembly metrics
  without changing the core workflow tables.
