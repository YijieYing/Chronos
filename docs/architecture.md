# Chronos architecture

Updated 2026-09-05. This document describes current ownership and extension rules, not a claim that
every planned capability ships. See the [feature audit](feature-audit-2026-09-05.md) for implementation
evidence and the [roadmap](roadmap.md) for acceptance gates. The audit includes uncommitted worktree changes.

## Canonical Agent path

```text
Prompt → Parser → Items → Interpreter → Events → Planner → Plan
       → Lowerer → Operations → Runtime → Schedule / Reminder
```

| Concept | Responsibility | Must not become |
| --- | --- | --- |
| Item | Exact Prompt fragment and source anchor; production Parser currently keeps the whole Prompt | An executable command |
| Event | Meaning produced by Interpreter, including unresolved fields and evidence | Another Plan or provider-specific command |
| Plan | Planner's resolved result, targets, constraints and conflicts | An API-specific duplicate plan |
| Operation | Finite executable primitive produced by Lowerer | Unresolved semantic intent |
| Runtime | Apply/revert authorized Operations and record execution | Another semantic interpreter |
| Proposal | Review/lifecycle view around the canonical result | A second authoritative executable payload |
| Log | Append-only interaction/execution view | A planning engine |
| Projection | Temporary timeline view of proposed changes | Committed Schedule data |

`AgentOperation` is the existing persisted lifecycle aggregate containing Snapshot, Plan and Operations;
it is distinct from an executable Operation primitive. Retain existing vocabulary rather than introducing
Intent, SemanticInterpretation or another synonymous layer.

Interpreter currently combines one model extraction with normalization into Events. This is a two-layer
responsibility, not two guaranteed model calls. Some source-text rules currently override valid model fields;
field-local evidence and stable clarification are P0 work, not already achieved guarantees.

A semantic Gap blocks execution of the batch. Missing targets can produce structured Plan conflicts.
Not all unsupported constraints currently become structured conflicts; exception handling and silent
deterministic fallback still need alignment with the intended clarification/retry policy.

## Domain ownership

### Schedule

Schedule owns tasks, constraints, deterministic daily planning, versioned Agendas and backend timeline
projection. It can operate without Monitor. Daily/weekly series are stored once and projected over a
bounded horizon; they are not an unlimited set of stored future rows.

Manual task writes use ScheduleService through the v1 API. Agent writes reach it through Runtime.
Schedule's Agenda versions are domain scheduling results; they are not an alternative Agent Plan.
Service mutations can trigger Schedule planning internally. Thus “Runtime does not invoke Agent Planner”
does not mean execution causes no downstream agenda computation.

Task completion/actual duration, recurring occurrence exceptions and long-term goals do not yet form
complete user workflows.

### Reminder

Reminder owns point/window triggers and reminder state without consuming Schedule capacity.
It shares task-like selection, property editing and deletion in the UI, but is not a zero-length
Schedule block. Duration is not a required Reminder field.

CRUD and Beacon rendering exist. Actual notification delivery, retry/receipts and Monitor-selected
interruption timing do not. Some delivery policy flags reach Plan/Operation but are not durably stored
in the current Reminder model. Agent reminder updates currently recreate the object and can reset state.

### Monitor

Monitor owns normalized Observations, bounded live aggregation, rule-based WorkState estimates,
activity segments and CognitiveState points. Native collectors supply input counts, foreground context
and session signals. Cognitive history is persisted; live activity segmentation is not equivalent to
a durable record of which scheduled Task the user actually performed.

Monitor does not write Schedule. Frontend MonitorAdapter currently generates forecast-like display
values; there is no authoritative backend Forecast integrated with Agent planning.
Production multi-device reconciliation and full late-evidence revision remain design work.

### Agent

Agent owns the canonical request pipeline, clarification, authorization policy, execution lifecycle,
Log and Projection. `State` currently carries only now/timezone; target drafts provide selected task/reminder
data, while the model-facing object index is limited. Accepted Memory/Profile is not yet wired into
canonical model requests. Neither general state-aware planning nor reliable grounded schedule queries
should be inferred from the existence of those components.

The adjustment engine detects and records selected passive signals without producing executable
schedule adjustments. Registry persistence exists but production Flow does not capture failures into it.
There is no separate implemented `adaptation/` domain directory; future proactive behavior should reuse
Agent Plan/Operations/Runtime rather than create a competing execution pipeline.

## Runtime and authorization boundaries

Interpretation/clarification and review do not themselves authorize arbitrary writes. The autonomy gate
can authorize eligible actions; other actions require confirmation. Runtime owns before/after records,
application and compensation. The v1 boundary marks overlapping proposals stale after timeline changes.

Current limitations:

- Cross-repository execution is not one crash-atomic SQLite transaction.
- Undo does not fully protect against subsequent object edits.
- Full task updates can default fields missing from TaskDraft.
- Stale proposals currently require resubmission rather than automatic safe recompilation.
- Automatic execution does not reliably refresh every committed frontend object view.

These are P0/P4 acceptance work. Do not describe all writes as confirm-only, all Undo as conflict-safe,
or all batches as atomically committed until those stronger contracts are implemented and tested.

## Current source layout

```text
src/chronos/
├── agent/           # Canonical pipeline and interaction lifecycle
├── schedule/        # Schedule domain; also contains remaining legacy Agent helpers
├── reminders/       # Reminder domain
├── monitor/         # Observation and state estimation
├── infrastructure/  # Persistence and adapters
└── api/             # Local HTTP and CLI composition

apps/mac-agent/      # Native Observation producer
apps/mac-app/        # WKWebView shell, not a domain engine
web/                # UI and read-model consumers
```

Legacy semantic/parser/proposal components remain in `schedule/`, and Runtime still imports helper
functions from `schedule/proposals.py`. Production composition constructs an old memory-aware parser
without using it for canonical requests. The old semantic/proposal execution route is not the primary
path, but “all legacy code deleted” is not an accurate repository status.

Any further compatibility work must identify the exact surviving dependency, its replacement and deletion
phase. P0 should remove obsolete composition and consolidate required helpers under their real owner
when touched; do not revive legacy parsing to implement a new capability.

## Extension rules

1. Add user scenarios and acceptance fixtures before expanding schema. Reuse existing Item/Event/Plan
   vocabulary and define field provenance, uncertainty and clarification behavior.
2. Extend read-only State/drafts for grounded queries, actual records and accepted context. Distinguish
   user statements, observed evidence and predictions; attach freshness/source.
3. Add a new Operation only for a genuinely new executable primitive. Its authorization, stale scope,
   persistence, idempotency, failure compensation and Undo must be specified together.
4. Keep notification delivery outside semantic interpretation; evaluator and adapters consume durable
   Reminder state and write delivery receipts.
5. Route proactive adjustments through the same Planner and Runtime. New evidence is not permission.
6. External integrations and optional vision remain adapters with explicit user consent. Raw observations,
   imported documents and model output must never become unvalidated write authority.

No code module/type names were added, replaced or deleted in this documentation round. The current
documentation removes obsolete claims that Adaptation is an implemented directory or that legacy
command/proposal models remain the canonical Agent path. Historical terms remain in archived designs.
