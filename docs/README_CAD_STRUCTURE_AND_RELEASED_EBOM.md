# CAD Structure and Released EBOM

Nexus controls one engineering structure and presents it in two ways. The existing BOM remains the authoritative CAD structure. The Released EBOM is a read-only, derived view of the exact checked-in CAD object iterations and occurrence bindings; it is not a second BOM database.

## CAD Structure

CAD Structure contains every controlled Creo `PRT` and `ASM`, including physical components, CAD-only grouping assemblies, reference objects, and skeletons. It remains the structure used by checkout, check-in, commits, snapshots, baselines, and named configurations. Each parent iteration binds every child occurrence to an exact child revision and iteration.

Organizational folders affect only CAD-tree presentation. They are not occurrences and never appear in a Released EBOM.

## Object policy and occurrence policy

Object policy is versioned with the BOM object in `bom_iterations.object_data_json`:

- `classification`: `PHYSICAL`, `CAD_ONLY`, `REFERENCE`, or `SKELETON`
- `default_ebom_behavior`: `NORMAL`, `FLATTEN`, or `EXCLUDE`
- `cad_requirement`: `REQUIRED`, `OPTIONAL`, or `NOT_REQUIRED`
- `drawing_requirement`: `REQUIRED`, `OPTIONAL`, or `NOT_REQUIRED`

Occurrence policy belongs to one parent-child usage:

- `ebom_behavior`: `INHERIT`, `NORMAL`, `FLATTEN`, or `EXCLUDE`

`INHERIT` resolves from the default behavior stored in the bound child iteration. An explicit occurrence value replaces that default. This permits the same `CAD_ONLY` assembly to flatten in one product while remaining visible in another.

In the item editor, **Not for delivery** is the user-facing form of an object default `EXCLUDE`. It does not add another delivery database or flag: it stores the existing version-controlled EBOM policy. The CAD Structure keeps showing the object with a **NOT FOR DELIVERY** badge, while Released EBOM omits the object and its descendants. For an occurrence-specific exception, use **Edit EBOM Behavior** on the checked-out parent occurrence.

New-object recommendations are delivery-safe: `CAD_ONLY` suggests `FLATTEN`, while `REFERENCE` and `SKELETON` suggest `EXCLUDE`. These are authoring recommendations only; migration never changes existing objects away from the compatibility defaults.

Changing an occurrence policy is a parent structure change. The parent must be checked out, and check-in freezes the policy in the new parent iteration alongside usage ID, child object, child revision/iteration, quantity, and sort order. Released parent iterations remain immutable.

## Resolution rules

- `NORMAL` emits the child as an EBOM row, then resolves its children beneath it.
- `FLATTEN` does not emit the child. Its resolved descendants are promoted to the current visible EBOM parent.
- `EXCLUDE` emits neither the child nor anything below it.
- `INHERIT` first resolves to the bound child iteration's stored default and then applies that rule.

Only resolved `NORMAL` nodes can become visible Released EBOM rows. The resolver enforces this invariant for the page, exports, baselines, named configurations, and effective where-used. A top-level CAD root marked `EXCLUDE` is omitted completely. A top-level root marked `FLATTEN` is omitted and its deliverable children become visible roots.

Repeated occurrences are preserved as separate rows. Resolution uses path-local cycle detection: legitimate reuse in separate branches is allowed, while a recursive ancestor loop produces a clear error.

## Quantities

Every EBOM row shows its source quantity and effective quantity. Effective quantity is the product of quantities along its path, including every flattened level.

For example, if Top uses a flattened CAD-only assembly with quantity 2 and that assembly uses Bolt with quantity 3, Bolt is promoted under Top with source quantity 3 and effective quantity 6. The row records which flattened occurrences caused the promotion.

## Versions, baselines, and configurations

Object policy is captured in each object iteration. Occurrence policy is captured in each parent iteration binding. Released EBOM resolution reads only those immutable values; it does not consult mutable current defaults for historical iterations.

Snapshots include object policies and frozen binding behavior. Baselines retain exact object iteration IDs and can resolve their selected roots from those iterations. Named CAD configurations retain a root iteration and copy occurrence behavior into their configuration members. Consequently, a historical baseline or configuration can reproduce the rules that existed when it was captured even after current object defaults change.

Project-version copying continues to remap and copy the same object revisions, iterations, and exact bindings, including occurrence behavior.

## CAD-only assembly workflow

1. Classify the object as `CAD_ONLY`.
2. Set its normal object requirements. New CAD-only authoring defaults recommend native CAD as `REQUIRED` and drawing as `NOT_REQUIRED`.
3. Set its default EBOM behavior, commonly `FLATTEN`.
4. Where a product needs a different result, check out the immediate CAD parent and use **Edit EBOM Behavior** on that occurrence.
5. Because an occurrence-policy change is a CAD-controlled structure change, update the corresponding native ASM through the unified commit/check-in workflow.
6. Check in the parent to create the immutable iteration used by Released EBOM resolution.

Requirement validation is per object iteration. A drawing is not globally required. A `CAD_ONLY`, `REFERENCE`, or `SKELETON` object with `drawing_requirement=NOT_REQUIRED` is not blocked for having no drawing; a field marked `REQUIRED` is enforced.

## Where used

Direct CAD where-used reports the immediate Creo assembly from `bom_children`. Effective EBOM where-used resolves the current checked-in structures and reports the first visible EBOM parent. If intermediate CAD-only assemblies flatten, the result includes their promotion path and multiplied effective quantity.

## MBOM boundary

EBOM `FLATTEN` is an engineering-definition rule only. Nexus does not interpret it as an MBOM phantom, routing, make/buy, or manufacturing-planning rule. Manufacturing structure remains a separate future concern and must use its own policy model.
