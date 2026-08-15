# Chronos engineering rules

## Canonical naming

- Naming is part of the architecture. Every module and core type should use the shortest precise domain term available, preferably one word.
- One responsibility has one canonical name. Do not maintain parallel terms for the same layer.
- Prefer the Chronos pipeline vocabulary `Intent → Plan → Operations → Runtime`.
- Use `Intent` for the semantic meaning of what the user wants. Do not add parallel concepts named `SemanticInterpretation`, `AgentInterpretation`, `IntentRepresentation`, or similar.
- Use `Plan` for the Planner's resolved result. Do not add proposal-specific or API-specific plan models that duplicate it.
- Use `Operation` for a finite executable primitive. Do not use it for unresolved semantic meaning.
- `Proposal`, `Log`, and `Projection` are views or lifecycle concepts around the primary pipeline; they must not own duplicate semantic or executable truth.
- Before creating a module or type, search the repository for overlapping vocabulary. Prefer renaming, merging, or deleting an existing concept over adding a synonym or adapter layer.
- A compound name is allowed only when every word represents a necessary domain distinction that a single word cannot preserve.
- Compatibility code must live behind an explicit `legacy` or `compat` boundary, name its target replacement, and include a deletion phase. New primary code must not depend on it.
- Each architecture phase must report names added, names replaced, and names deleted. A new name that leaves an equivalent old concept authoritative is not a completed migration.

