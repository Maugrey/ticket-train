# Scope and specification decisions

Implement behavior explicitly requested by the source, mandated by the project,
already approved by the user, or technically necessary to achieve that behavior.
Routine internal structure is a technical choice. A different visible behavior,
missing functional default, reduced acceptance criterion, migration, compatibility
strategy or data-preservation policy is a specification decision.

Record each proposed deviation with a stable ID, source gap, concrete options,
full option meanings, consequences and recommendation. Preserve the original
source and provide one explicit selection per deviation. A/B labels alone are
not a durable decision. Auto approval modes do not authorize specification changes.

Use existing lifecycle context. A prototype may legitimately have no production
data or backward compatibility requirement. Do not invent migrations, backfills,
rollout bridges, dual writes, legacy support or generic abstraction to resolve an
imaginary production problem. Conversely, preserve requirements actually stated
for the project. Missing material context warrants a focused question.

The analysis event's `scope_assessment` traces authorized items and proposals.
The controller stores decisions against source revision and concrete options.
The same source and options can reuse their selected behavior after a retry.
A materially changed option or source requires a new binding, not silent reuse.
Carry decision references into implementation and verification contract revisions.

Later phases remain bound to the authorized assessment. A newly discovered
specification ambiguity returns an input request with exact blocked scope,
continuing independent scope and a concrete question. Do not stop unrelated work
when dependencies allow it. Technical repairs within the existing contract do not
create a new product approval requirement.

Changes suggested by AI model: GPT-6 (Codex).
