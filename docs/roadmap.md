# Chronos roadmap

This document is the single backlog for valuable capabilities that should be remembered but are
not part of the current implementation. Each item should state its current limitation, intended
outcome, and delivery checkpoints. Completed work should move to release notes or architecture
documentation instead of remaining here.

## Agent understanding and planning pipeline

### Agreed architecture

Chronos will migrate to one forward-only pipeline. Each stage consumes primarily the structured
output of the previous stage and must not repeat that stage's responsibility:

```text
Prompt
  -> Parser
  -> Items
  -> Interpreter
  -> Events
  -> Planner + State
  -> Plan
  -> Lowerer
  -> Operations
  -> Gate
  -> Runtime
  -> Schedule / Reminder domain

Monitor + Timeline + Forecast + Profile
  -> Estimator
  -> State
```

Proposal, Projection, and Log are lifecycle views around this pipeline. They must not own a second
semantic model, planning payload, or executable truth. Runtime executes only the finite Operations
produced by Lowerer; it does not interpret language, call Planner, or reconstruct planning objects
from lower-level operations.

### Layer boundaries

- **Parser** only segments the original Prompt. It does not infer kind, request, time, duration, or
  title. It asks only when the segmentation boundary itself is ambiguous.
- **Item** is an exact contiguous source fragment with `id`, `promptId`, `span`, and `text`. Its text
  must equal the corresponding Prompt slice.
- **Interpreter** understands each Item. It may combine multiple Items into one Event or emit
  multiple Events from one Item. It owns linguistic interpretation; Planner must not normally
  reinterpret the original Prompt.
- **Event** is the complete semantic description understood from one or more Items. It retains
  evidence and provenance rather than model-generated replacement wording.
- **Estimator** interprets changing user information from Monitor, Timeline, Forecast, and Profile
  and produces State independently of Prompt interpretation.
- **Planner** consumes Events, State, and the necessary Schedule view. It resolves constraints,
  raises all post-Parser clarification centrally, records assumptions, and produces one Plan.
- **Lowerer** deterministically converts Plan into the finite executable Operations union.
- **Gate** decides proposal versus direct execution from ambiguity, impact, reversibility, risk,
  and the selected autonomy level.
- **Runtime** validates and transactionally executes Operations, writes Log entries, and supports
  rollback and Undo.

### Item, Event, and request decisions

An Item contains no inferred action or timing fields. An Event contains source-backed `content[]`,
one semantic `request`, `time`, optional `duration`, references, relations, gaps, residue, and
provenance.

For a concrete timeline Event, the semantic request vocabulary is limited to:

- `add`
- `edit`, with a target and the Event fields intended to change
- `delete`, with a target

These are user-level requests, not Runtime operations. Lowerer remains responsible for translating
the Plan into executable primitives. Inputs that ask Chronos to answer rather than mutate a
timeline object are Directives; their responses appear as first-class Chronos Log entries and may
carry clickable Timeline references, but do not create Operations merely to display a reply.

Time must not use `null` for every non-concrete case. The initial vocabulary distinguishes:

- no time was expressed
- symbolic `morning`, `afternoon`, or `evening`
- a concrete point or range
- flexible scheduling such as “when available”
- relative timing linked to a Relation, such as “after A”
- expressed but unresolved timing

Approximation is precision attached to a point, range, or symbolic time rather than one catch-all
time type.

If the user says that B is included with A, Interpreter combines their source fragments into one
Event, keeps the already established duration, and derives display text deterministically from the
combined content. This does not require shared-duration types, `same_block`, or a merge Runtime
operation. A relation is retained only when the events must remain independently identifiable.

### Clarification and residue

Parser clarification anchors the ambiguous source span because Items do not yet exist. Every
clarification after Parser must anchor a concrete Item and may additionally reference an Event or
related Items. Answers return to the stage that owns the uncertainty and produce a complete new
snapshot; Planner must not patch Event semantics itself.

Interpreter must respond to every Item. When it cannot safely express part of the language, it
emits typed Residue with the original span and reason instead of inventing a value or failing the
whole request. Planner may inspect Residue only to decide among:

1. continue with an explicit Plan assumption;
2. ask an Item-anchored clarification;
3. return a typed capability failure.

Planner must not silently turn Residue into asserted Event semantics. Assumptions belong to Plan;
facts inferred from Prompt or clarification belong to Event. Provenance records whether each fact
or assumption came from Prompt, clarification, selection, Profile, Timeline, Monitor, or planning.

Every Residue occurrence must be persisted in a capability-gap registry with its sanitized source
example, reason, affected Item/Event, handling outcome, and Interpreter version. During early
development this registry will be reviewed regularly to expand Interpreter coverage and convert
real examples into regression tests, steadily reducing how often Planner sees Residue.

### Ready to implement next

These changes have agreed semantics and can begin without another product-design round:

1. Add characterization tests for the current Prompt-to-Runtime path and architecture-boundary
   tests that forbid lower-to-higher reconstruction.
2. Define the minimal Parser, Item, Event, Request, Time, Gap, Residue, Provenance, Directive, and
   State contracts using the canonical short names above; identify the legacy types each replaces.
3. Implement exact-span Parser output and tests proving Parser does not infer Event fields.
4. Implement the first Interpreter slice for task/reminder `add`, `edit`, and `delete`, symbolic
   periods, concrete ranges, flexible/relative/absent/unresolved time, and Item-anchored gaps.
5. Persist Residue capability gaps and add a review/export command plus regression fixtures.
6. Route clarification answers back through Interpreter to produce full Event snapshots; remove
   field-filling behavior from the migrated slice.
7. Establish one Plan output and one deterministic Lowerer path for the first vertical case:
   “下午安排半小时日语”. Runtime must receive only Operations.
8. Migrate “A 和 B 算在一起，总共一小时” through combined Event content, without a merge
   operation or a second proposal payload.
9. Render Directive answers in Chronos Log, including clickable object and time-range references.
10. For every migration phase, report types added, replaced, and deleted, plus any explicit compat
    path and its removal checkpoint.

### Later rounds

These require more design or depend on the first vertical slices:

1. Settle the detailed Event dimensions beyond the initial request/time vocabulary, including
   recurrence, priority, rigidity, cognitive properties, long-term planning, and reminder policy.
2. Expand Estimator and State contracts for Monitor evidence, current activity, predicted cognitive
   load, uncertainty, and freshness without exposing unnecessary raw data to Planner.
3. Support broader edit and replanning cases, multiple related Events, historical recording,
   deadlines, preferences, and constraint-aware changes while keeping the same forward-only path.
4. Move Proposal, Projection, and Log completely onto Event/Plan/Operations references, then remove
   legacy proposal execution payloads, task reconstruction, and duplicated state.
5. Add capability dashboards and quality metrics for Residue rate, clarification rate, unsupported
   concepts, false assumptions, and coverage by Interpreter version.

### Long-term automation

Introduce an **Updater** only after the Residue registry and manual improvement loop are stable.
Updater will cluster recurring capability gaps, propose Event vocabulary or Interpreter changes,
generate candidate fixtures, and present a reviewable patch. It must not modify production schemas,
prompts, or Interpreter behavior autonomously. Human approval, versioning, evaluation, and rollback
remain required.

Updater is an offline development loop, not another stage in the request-time pipeline:

```text
Residue registry
  -> Updater
  -> reviewed Interpreter change
  -> regression suite
  -> versioned release
```

### Architecture invariants

- Raw Prompt is visible to Parser; Item text is visible to Interpreter. Planner normally receives
  Events rather than the full Prompt.
- Interpreter owns language meaning; Planner owns scheduling decisions.
- Event facts and Plan assumptions are never stored as the same truth.
- Data moves only toward more concrete representations: Items -> Events -> Plan -> Operations.
- Operations are never reconstructed into Events or Planner input on the primary path.
- Proposal, Projection, and Log reference the same Event, Plan, and Operations owned by an
  AgentOperation.
- An unsupported phrase becomes Residue, clarification, or a typed failure—not an invented
  executable operation.

## Context-aware Reminder delivery and object conversion

### Current limitation

Reminder / Beacon supports point and window triggers, exact/context-aware delivery intent, timeline
and Overview markers, and Agent proposals. The first version does not yet use Monitor signals to
select an actual delivery instant inside a window. It also does not aggregate dense reminders or
convert between Task and Reminder.

### Intended outcome

- Select a natural interruption point from task completion, focus decline, recovery state, low
  cognitive load, and absence of fixed work.
- Add an explicit interruptibility threshold and an auditable reason for the selected delivery time.
- Aggregate dense Overview beacons without turning them into calendar blocks.
- Convert Reminder ↔ Task while preserving title, temporal information, and Chronos Log history.

### Delivery checkpoints

1. Add Monitor-driven reminder evaluator and idempotent delivered events.
2. Add interruptibility policy, cooldowns, delivery explanation, and notification adapter.
3. Add Overview aggregation and reminder status controls.
4. Add reversible Task / Reminder conversion commands and UI.

## Background monitoring independent of the UI

### Current lifecycle

Chronos monitoring is currently owned by the desktop app launcher. `scripts/run-mac-app.sh`
starts the Schedule API and monitor collector as child processes and its exit trap terminates the
processes it started. Closing Chronos therefore stops new observations. Existing observations stay
in SQLite and remain available on the next launch.

While the collector is running it can record session, screen-sleep, system-sleep, and wake
boundaries. A physically sleeping Mac cannot observe user activity; the meaningful future promise
is continuous collection while the UI is closed, locked, or idle, plus an explicit gap bounded by
sleep and wake events.

### Intended outcome

This is a planned capability, not implemented yet.

- Split the local Schedule/Monitor service lifecycle from the Chronos window lifecycle.
- Follow Octopus's proven service pattern: detached process, PID file, startup lock, bounded logs,
  process-identity-safe stop, health probing, and restart recovery.
- For a production macOS install, supervise the collector at login with `SMAppService` or a
  per-user LaunchAgent instead of relying only on a shell-owned daemon.
- Keep a local append-only observation buffer in the collector and replay it idempotently when the
  API becomes available, so a service restart does not create a data hole.
- Emit explicit `sleep`, `wake`, `screen_locked`, and `screen_unlocked` boundaries and represent
  physical sleep as an unknown gap rather than invented activity.
- Preserve local-only storage, permission visibility, retention limits, and a clear pause/quit
  control before enabling background collection by default.

### Delivery checkpoints

1. Extract start/status/stop commands for the local service and add identity-safe PID/lock/log
   management.
2. Add collector buffering and idempotent observation ingestion.
3. Add login supervision and a UI control that distinguishes closing the window, pausing capture,
   and fully quitting the background service.
4. Test app-close, screen-lock, sleep/wake, API restart, and machine restart scenarios.

## Personal context and memory sync for Chronos Agent

### Current limitation

The Agent now loads a Git-ignored personal Markdown profile through a content-hash cache. It accepts
GPT/Claude-generated Markdown profiles and ChatGPT/Claude export ZIPs, retains them locally, parses
them without model upload, and presents reviewable candidates. Imports flag possible updates and
conflicts; accepted items can be edited or forgotten. Request-time local retrieval selects relevant
accepted memories and proposals persist the exact context used. The Agent does not yet synchronize
directly from Codex, ChatGPT, Claude, Octopus, or other live sources.

### Intended outcome

- Keep dynamic memory separate from the Markdown profile: store versioned facts and summaries with
  source, timestamp, confidence, sensitivity, and optional expiry.
- Retrieve only context relevant to the current request instead of sending the complete personal
  history to an external model on every call.
- Add import/export and synchronization ports so Codex/ChatGPT-reviewed summaries, notes,
  calendars, Schedule, Monitor summaries, and selected Octopus sources can contribute context
  without becoming one untraceable database.
- Let the user inspect, correct, forget, pause, or exclude any memory source. Never infer a durable
  personal fact from one observation without confirmation.
- Keep raw activity local by default; external providers receive only the minimum selected context
  needed for the current command.

### Delivery checkpoints

1. Expose a reverse-sync MCP/App tool so ChatGPT, Codex, or Claude can submit reviewed candidate
   memories to Chronos from inside an authenticated conversation without sharing account cookies.
2. Keep provider OAuth adapters behind a capability interface. Enable direct account sync only when
   a provider publishes an authorization scope and API for conversations or memories; ordinary
   “Sign in with ChatGPT” identity is not treated as data access.
3. Add opt-in adapters for Schedule, Monitor summaries, notes, calendar, Codex/ChatGPT exports, and
   Octopus imports.
4. Add stronger conflict resolution, retention controls, redaction, encrypted-at-rest options, and
   reversible restore for edited or forgotten context.

### Provider account constraints

- Do not scrape ChatGPT or Claude web interfaces, reuse browser cookies, automate MFA, or store
  consumer-session tokens.
- ChatGPT and Claude account exports are asynchronous user-controlled archives today, so archive
  import is a refresh workflow rather than continuous OAuth polling.
- Store a source cursor/hash and show only additions, changes, contradictions, and possible removals
  since the last accepted import. Never turn an imported chat directly into durable memory.

## Backend Forecast and observed-task identity

Phase 12 routes reliable Schedule and Monitor evidence into passive Agent Operations, but the
current Forecast remains a frontend visualization calculation. Before enabling `task_overrun`,
`schedule_drift`, or automatic replanning:

1. Persist Monitor's observed/current task identity with confidence instead of retaining only a
   broad activity type in CognitiveState.
2. Move predicted task end and overrun confidence behind a versioned backend Forecast contract.
3. Compare committed Schedule blocks with observed task intervals to emit evidence-backed drift and
   overrun signals.
4. Replace the passive replan compiler with a replanning compiler that produces strict IR and sends
   it through Autonomy Gate, Proposal/Projection, Runtime, and Undo.
5. Keep proactive Log/UI presentation separately opt-in; signal capture must continue when the Log
   is folded or the UI is not running.
