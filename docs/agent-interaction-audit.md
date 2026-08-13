# Chronos Agent Interaction System — Phase 0 Architecture Audit

Status: completed audit, 2026-08-13. This document describes the repository before the Operation
foundation is introduced. It is a migration contract, not a claim that later phases already exist.

Implementation progress:

- Phase 1 completed: immutable Agent/IR contracts and versioned strict serialization.
- Phase 2 completed: lifecycle validation, optimistic versioning, SQLite Operation persistence,
  parallel pending queries, and scope-based stale marking.
- Phase 3 completed: append-only Chronos Log persistence, proposal/manual event ingestion,
  Operation-linked pending counts, legacy proposal history import, TimelineReference navigation,
  and Collapsed/Peek/Expanded frontend states.
- Operation/Log foundations now bridge the legacy proposal workflow; Compiler and Runtime wiring
  still begins in later phases.

## Current architecture

### End-to-end request path

```text
AgentInput
  -> POST /api/v1/proposals { text }
  -> ProposalService.create
  -> SemanticScheduleCommandParser / deterministic parser
  -> AgentInterpretation
  -> planner preview or Reminder draft
  -> schedule_proposals.payload_json
  -> frontend ScheduleProposal
  -> AgentCommand + derived ChronosLogEntry
  -> accept/reject/restore endpoint
  -> ProposalService mutates ScheduleService or ReminderService
  -> frontend reloads Timeline + Reminder projections
```

This path already enforces the most important safety boundary: the model produces interpreted data,
not SQL, and Schedule planning/validation remains deterministic. Agent-created mutations wait for
confirmation and accepted Schedule batches use an atomic SQLite repository method.

The path is not yet the target Operation architecture. `ProposalService` currently owns compilation,
proposal construction, freshness checks, execution, and restore. A proposal JSON document is used as
interpretation output, working state, UI contract, and partial undo record at once.

### Capability inventory

| Area | Current implementation | Reusable part | Missing target boundary |
| --- | --- | --- | --- |
| Timeline store | `useTimelineStore` owns tasks, reminders, one command list, derived logs, focus target, and optimistic writes | Existing API adapters and reload behavior | No OperationStore, SelectionStore, ProjectionStore, or transaction client |
| Schedule API | Versioned plans, typed Task model, deterministic planner, preview/apply helpers, stale plan version checks | Planner and Schedule service remain authoritative | Runtime facade must execute validated IR rather than proposal-shaped dictionaries |
| Task model | Task, recurrence, preferred start, `fixed`, planner blocks | Stable IDs, recurrence expansion, plan versions | Recurrence, rigidity, window, and adjustment policy are not independent yet |
| Reminder model | Independent Reminder domain, point/window trigger, delivery intent, status | Keep separate from ScheduleBlock | No update/move operation, scope version, delivery runtime, or transaction batch |
| Agent interpretation | Semantic parser produces source-grounded `AgentInterpretation`; memory retrieval records context used | Provider adapters, provenance verification, memory retrieval | Input is text + tasks only; no full InteractionContext or stable Chronos IR |
| Clarification | Persisted proposal status `needs_clarification` with unresolved questions | Existing source-grounded missing-field detection | No operation snapshot refresh, answer linkage, quick options, or parallel clarification UX; renderer is still a modal path |
| Proposal | Persisted `schedule_proposals` JSON, planner preview, accept/reject, plan freshness guard | Existing proposal records can be adapted during migration | Proposal is a separate status vocabulary and payload instead of one AgentOperation state |
| Projection | `proposed_task` chooses command position; `PredictionShadow` renders forecast extension | Timeline coordinate system and task waveform primitives | No typed Agent projection layer or proposal/incomplete ghost renderer |
| Selection | Task click opens editor; Reminder click pins its label; Overview can pan to a time | `focusTarget` already pans the timeline | No selected object, contextual command bar, Reminder selection, or time-range selection |
| Chronos Log | Expanded drawer derives entries from proposals and appends manual events in React memory | Existing product name and restore affordance | No persisted event stream, typed log union, operation linkage, references, Peek state, or pending badge |
| Undo / restore | Proposal restore methods and frontend before-object restoration | Schedule batch removal and before payloads provide migration material | Not a general transaction; manual logs are volatile and multi-domain writes are not atomic |
| Direct manipulation | Overview drag/resize writes Schedule immediately and adds a local log entry | Existing manipulation mechanics | Bypasses Operation/Runtime and cannot stale overlapping pending work |
| Monitor / Forecast | Cognitive state history and forecast are read models used by the frontend | Can populate future InteractionContext | Compiler receives neither current cognitive state nor forecast today |
| Personal context | Accepted memory retrieval is hash-cached and request-relevant | Keep retrieval adapter | Needs a typed, minimal `userProfile`/context snapshot in compiler input |
| Autonomy | Every Agent mutation currently requires confirmation | Safe default during migration | No persisted autonomy level, risk/ambiguity/impact gate, or direct execution path |

### Current lifecycle semantics

The backend currently persists these proposal statuses:

```text
needs_clarification | pending | accepted | rejected | restored | informational
```

They only partially map to the target Operation lifecycle:

| Existing proposal status | Future Operation state | Migration note |
| --- | --- | --- |
| `needs_clarification` | `awaiting_clarification` | Preserve questions and request text; version starts at 1 |
| `pending` | `proposed` | Convert draft commands/reminders into typed TimelineOperations |
| `accepted` | `completed` | Existing records have no separate approved/executing events |
| `rejected` | `rejected` | Direct mapping |
| `restored` | `completed` transaction plus later UndoLog | Do not model restore as an Operation terminal state |
| `informational` | `completed` with informational intent/result | Contains no TimelineOperation |

There is no durable `interpreting`, `ready`, `approved`, `executing`, `failed`, `cancelled`, or
`stale` state. Freshness is checked only when a proposal is accepted; stale proposals are rejected by
exception but not persisted as stale.

### Persistence and transaction audit

- `schedule_proposals` stores one opaque JSON payload per proposal. It can preserve legacy records
  during migration but cannot efficiently query state, scope, version, or pending count.
- Schedule recurring-task batches and their daily plans are written atomically in one SQLite
  connection. This is the strongest existing transaction primitive and should be reused by Runtime.
- Single Schedule create/update methods perform compensating rollback in Python, but plan updates and
  later Log/Operation writes do not share one database transaction.
- Reminder proposal acceptance loops over reminders one by one. It is not atomic with proposal state
  or Chronos Log persistence.
- Manual task changes are written immediately. Their log entries and before-state exist only in the
  browser and disappear after reload.
- `timeline_tasks` remains a legacy import source; Schedule tasks and activated plans are the actual
  Timeline write model. The Operation foundation must not revive it as a second write model.

### UI state audit

- The bottom command bar sends only a string. It has no selection chip or operation target.
- Only the newest pending proposal is restored into `commands` on startup. `runAgent` also removes
  other proposed commands, so parallel pending operations are not currently supported in the UI.
- Clarification can still render through the full-screen portal in `TimelineCommand`; startup now
  suppresses historical clarification, but a newly created one is modal.
- `ChronosLog` has only collapsed (not mounted) and expanded drawer states. There is no Peek state or
  pending-operation badge.
- Log entries have ad-hoc optional task fields rather than typed TimelineReferences and are not
  clickable for timeline selection.
- `PredictionShadow` is a forecast visualization for committed tasks. It is not an Agent proposal
  ghost and must not be repurposed as the Operation projection source of truth.
- Task clicks mean “edit”, not “select”. Reminder clicks only expand a label. Empty Timeline clicks
  open object creation; press-drag range selection does not exist.

## Migration plan

The migration is additive. Existing Schedule, Reminder, Monitor, planner, provider, and memory code
stay in place while orchestration moves out of `ProposalService` behind compatibility adapters.

### Phase 1 — Core data model

Create a backend-owned `chronos.agent` domain containing immutable dataclasses/enums for operation
state, intent snapshots, typed TimelineOperation union, scope, references, projections, proposal
snapshots, log events, transactions, autonomy policy, InteractionContext, and ReplanSignal. Add
explicit serialization and validation; mirror only API-facing contracts in TypeScript. No UI change.

The first primitive union will include the requested Task/Reminder/recurrence operations. Defer,
shrink, and split remain valid IR primitives even if Runtime initially rejects them as unsupported.
This lets validation distinguish “valid IR but unsupported runtime capability” from invented IR.

### Phase 2 — Operation store and state machine

Add normalized operation persistence plus versioned full snapshots. Keep `schedule_proposals` readable
and add a legacy adapter that maps old records into operation views. New requests write operations;
do not destructively migrate or reinterpret historical proposal JSON. State transitions are owned by
one service and invalid transitions fail before persistence.

### Phase 3 — Chronos Log

Add an append-only persisted log table keyed by `operation_id`, with serialized TimelineReferences.
Replace proposal-derived and browser-only log entries incrementally. Keep the current drawer as the
Expanded view, then add Collapsed/Peek and pending counts from OperationStore.

Implemented without replacing the proposal workflow: proposal lifecycle events are appended by the
backend, manual edits append through the same API, and old proposal rows are imported once as one
compatibility snapshot. The badge counts operations requiring action rather than history entries.
Peek shows the newest actionable event; Expanded exposes references that pan the shared Timeline.
Selection, projection, and Runtime-owned transactions remain in their later phases.

### Phase 4 — Timeline selection context

Introduce one selection union in the frontend. Change Task/Reminder clicks from immediate properties
opening to selection, with explicit properties/edit actions. Add press-drag empty-range selection and
send the selected reference in InteractionContext. Keep single-click empty creation only when no drag
threshold is crossed.

### Phase 5 — Timeline projection layer

Render typed projections supplied by operations. Projections live outside Log UI state and disappear
only through operation lifecycle rules. Existing task/reminder renderers remain unchanged; dedicated
incomplete/proposed renderers share timeline coordinates but never mutate committed data.

### Phases 6–7 — Compiler interface and LLM adapter

Define `ChronosCompiler.compile(InteractionContext) -> CompilerResult` before moving semantic parsing.
Wrap the existing provider, provenance validation, and memory retrieval behind that port. Replace
`AgentInterpretation` gradually with full Operation snapshots; retain a compatibility compiler for
existing deterministic commands. Compiler output never calls repositories.

### Phases 8–9 — Clarification and proposal UX

Remove the clarification portal. Answers target an operation ID and cause a complete versioned
recompile. Multiple pending operations remain visible in Peek/Expanded Log and retain independent
projections. Scope intersection marks impacted snapshots stale before they can execute.

### Phases 10–11 — Autonomy and Runtime

Move validation, constraint checks, autonomy gating, execution, transaction capture, rollback, and
Log writes into `ChronosRuntime`. Initially default migrated users to Level 0 to preserve present
behavior. Add direct execution only after risk, ambiguity, impact, and reversibility are persisted and
tested. Runtime delegates planning to Schedule and Reminder writes to Reminder, never to the LLM.

### Phase 12 — Adjustment integration

Define signals in Phase 1 but keep production emission disabled. Later, Adaptation creates Operations
through the same store/compiler/runtime ports; it never writes Timeline data directly.

## Files and modules affected

### New modules planned

```text
src/chronos/agent/
  models.py          # Phase 1 immutable contracts
  serialization.py   # Phase 1 strict versioned wire format
  state_machine.py   # Phase 2 transitions
  ports.py           # repositories, compiler, runtime ports
  service.py         # OperationStore application service
  runtime.py         # Phase 11 validation/execution
  policy.py          # Phase 10 autonomy gate

src/chronos/infrastructure/
  sqlite_operations.py
  sqlite_chronos_log.py
  sqlite_transactions.py

web/src/agent/
  types.ts
  operationApi.ts
  operationStore.ts
  selectionStore.ts
  projectionStore.ts
```

Names may be consolidated when a phase proves a module would contain no behavior. The bounded
context remains `agent`; it must not become a generic shared folder.

### Existing modules to migrate, not rewrite

- `schedule/semantic_parser.py` and `agent_interpretation.py`: become Compiler adapter internals.
- `schedule/proposals.py`: compatibility facade, then progressively delegates to OperationService
  and Runtime.
- `infrastructure/sqlite_proposals.py`: retained for historical read compatibility.
- `api/routes/v1.py`, `api/contracts/*`, `api/schedule_server.py`: add operation/log/context endpoints
  without breaking current proposal endpoints during migration.
- `web/src/state/timelineStore.ts`: shrink to committed Timeline data; move Agent, selection,
  projection, and log state to their owning stores.
- `web/src/components/Agent/*`: replace modal clarification and proposal-specific command cards with
  Operation Peek/Log controls.
- `WaveTimeline.tsx`, `TaskWave.tsx`, `ReminderBeacon.tsx`: add selection/projection inputs while
  preserving committed rendering and pan/zoom behavior.
- `schedule/models.py`, planner, and SQLite schema: later separate recurrence from rigidity and add
  adjustment policy through backward-compatible defaults.

## Compatibility risks and mitigations

1. **Historical proposal compatibility.** Existing JSON has no operation version/scope. Keep it
   immutable and adapt on read; never overwrite it with guessed fields.
2. **Duplicate source of truth.** Running ProposalService and OperationStore as peer write paths would
   diverge. New endpoints must delegate through one facade during the transition.
3. **Plan freshness versus scope freshness.** Current date-version checks do not cover Reminder or
   object-level overlap. Phase 2 stores explicit scope; Phase 9 adds scope invalidation before apply.
4. **Cross-domain atomicity.** Schedule, Reminder, Operation, Log, and Transaction repositories each
   opening their own SQLite connection cannot form one transaction. Runtime needs a unit-of-work port
   over the shared database before autonomy/direct execution is enabled.
5. **Undo fidelity.** Current restore recomputes plans and manual before-state is browser-only. New
   transactions need full relevant Schedule/Reminder snapshots and version checks.
6. **Parallel operations.** Frontend logic currently replaces other proposed commands. Do not expose
   parallel UI until backend state/query semantics and independent resolve endpoints are ready.
7. **Selection gesture conflicts.** Range drag competes with pan, Task editing, and click-to-create.
   Use movement thresholds, preserve Space+drag pan, and make event ownership explicit.
8. **Reminder hit testing.** The visual marker is intentionally small. Add a larger transparent button
   hit area without changing the beacon silhouette or blocking nearby Task interactions.
9. **Recurrence/fixed coupling.** Exact clock time currently implies `fixed` in semantic parsing, and
   the manual form uses `fixed` to derive meeting type. Add rigidity/window/policy fields with legacy
   mapping; do not silently reinterpret stored fixed tasks.
10. **Runtime capability skew.** IR may contain primitives that an early Runtime cannot execute.
    Validation must return a typed unsupported-capability failure, never partially apply the union.
11. **External model context/privacy.** InteractionContext can become large and sensitive. Build the
    full local snapshot, then send only compiler-relevant, auditable fields through the provider port.
12. **Frontend/backend version skew.** Continue capability negotiation in `/api/health`; add an
    `agent-operations-v1` capability only when the compatible endpoints are actually live.

## Phase 1 entry criteria

- Runtime behavior and database schemas remain unchanged by Phase 0.
- The new model must be backend-owned, immutable, strictly validated, JSON round-trippable, and
  independent of React, SQLite, ScheduleService, and any LLM provider.
- Operation lifecycle rules are declared with the model but transition behavior belongs to Phase 2.
- Current proposal statuses and payloads remain supported until their compatibility adapter is
  tested in a later phase.
- ReplanSignal and task adjustment policy are contracts only; Phase 1 must not activate proactive UI
  or change planner behavior.
