# Ticket Sources

## Contents

- Supported inputs
- Resolution rules
- Canonical ticket record
- Selection and ordering
- Source consistency
- Source update policy

## Supported inputs

Accept these ticket sources when explicitly named by the user:

1. A ticket list included in the invocation.
2. A local repository file, such as a Markdown backlog.
3. GitHub issues, milestones, projects, labels, or explicit issue identifiers when an authorized GitHub connector or CLI is available.
4. Another tracker, such as Linear or Jira, only when its authorized connector is available and the user identifies the workspace and selection.

The first version must work without an external tracker by supporting explicit lists and local files.

## Resolution rules

Resolve the source in this order:

1. Use explicit tickets in the invocation when present.
2. Otherwise use the explicit source plus selection or filter.
3. If a source exists without a selection, ask which tickets to select.
4. If no source exists, ask the user for one before any analysis.

Never infer the source by scanning TODO comments, filenames, arbitrary documentation, or issue trackers.

Echo the resolved source and selection before starting.

## Canonical ticket record

Normalize every ticket to:

```text
id
title
description
priority
acceptance_criteria
declared_dependencies
references
source_type
source_locator
source_revision_or_snapshot
source_order
```

Preserve the original ticket wording. Do not silently invent acceptance criteria. If material criteria are missing, report that during triage and analysis, raise provisional complexity conservatively, and apply the intrinsic-criticality criteria only to plausible consequences rather than ambiguity alone.

## Selection and ordering

Honor explicit user selection first.

For a file source, use documented ordering rules such as:

- an explicit recommended order section;
- priority groups;
- dependency annotations;
- source order when no stronger rule exists.

For an external tracker, use only the user-specified project, milestone, labels, cycle, filter, or ticket identifiers.

Do not exceed the train limit merely because the source returns more tickets. Select the requested bounded set and report the remainder.

## Source consistency

Record a source locator and revision or snapshot at analysis time.

Before implementation, detect material source changes when possible. If the description, acceptance criteria, priority, or dependencies changed, return the ticket to analysis and report the difference.

Treat the repository checkout as the implementation truth. An old ticket may be obsolete, partially implemented, or contradicted by newer project guidance. The analysis must verify applicability.

## Source update policy

Treat ticket sources as read-only by default.

Keep these states distinct:

```text
analyzed
approved
implemented on ticket branch
merged into train
delivered in base branch
closed in source
```

Do not edit a local backlog, close an issue, move a tracker item, or mark a ticket delivered because it entered the train.

Update source status only when the user explicitly authorized that action and the workflow reached the authorized delivery state.
