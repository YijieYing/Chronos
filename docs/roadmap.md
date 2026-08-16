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

### Contract map

The following contracts are the implementation checklist for the diagram above. “Reads” is a
deliberate visibility boundary: a layer should not receive earlier raw data merely because it is
convenient.

#### Prompt

- **Contains:** one immutable user submission, its id, original text, timestamp, selection, and
  Operation id.
- **Read by:** Parser. Selection is also available to Interpreter as bounded reference context.
- **Must not become:** the semantic source of truth after Events exist.

#### Parser

- **Reads:** Prompt text and nothing from Schedule, Monitor, Profile, or executable Operations.
- **Produces:** ordered Items whose spans point into the exact Prompt, or one source-span-anchored
  Boundary clarification.
- **Owns:** segmentation only.
- **Must not:** infer titles, kinds, requests, time, duration, relations, priority, or planning
  actions; rewrite source text; publish partial Items while a Boundary clarification is pending.

#### Item library

The canonical minimal Item is:

```text
Item {
  id
  prompt_id
  span { start, end }
  text
}
```

- `text` must equal the exact Prompt slice at `span`.
- Items preserve language evidence; they are not task drafts.
- One Item may later produce multiple Events. Multiple Items may later be combined into one Event.

#### Interpreter

- **Reads:** Items, current selection, clarification answers, and a minimal object index needed to
  resolve references. It does not receive Monitor, Forecast, a complete Schedule plan, or Runtime
  state by default.
- **Produces:** a complete new snapshot of Events and Directives, including source evidence,
  provenance, Gaps, and Residue.
- **Owns:** linguistic meaning—what each Item describes, whether it is a timeline Event or a
  Directive, Event kind, semantic add/edit/delete request, time expression, duration, reference,
  and relation.
- **Must:** return a typed result for every Item, even when that result is unknown or contains
  Residue.
- **Must not:** choose a concrete available slot, optimize the Schedule, emit executable
  Operations, or silently invent unsupported meaning.

#### Event library

The first Event vocabulary is:

```text
Event {
  id
  item_ids[]
  content[]       // exact Item-backed fragments
  kind            // task | reminder | state | schedule | unknown
  request         // add | edit(target, fields[]) | delete(target)
  time
  duration?
  references[]
  relations[]
  gaps[]
  residue[]
  provenance[]
}
```

- Event is semantic, not executable. `request: edit` is what the user wants changed; it is not an
  UpdateTaskOperation.
- Event content remains source-backed. Display text may be deterministically composed from several
  content fragments, but the model does not replace them with a new title.
- An Event may be partial when its uncertainty is explicitly represented by Gap or Residue.
- Combining “A and B count together” produces one Event with both content fragments and the
  established duration. It does not create shared-duration schema or a merge Operation.

#### Time library

```text
Time =
  none
  | period(morning | afternoon | evening, precision)
  | point(timestamp, precision)
  | range(start, end, precision)
  | flexible(optional period)
  | relative(relation_id)
  | unresolved(source text)
```

- `none` means the user expressed no time.
- `unresolved` means the user did express time but Interpreter could not represent it safely.
- Approximation is precision on a known time shape, not a synonym for missing or unresolved.
- Relative time links to a semantic Relation; Planner resolves it to concrete time later.

#### Gap

- **Means:** Interpreter understands the semantic shape but a specific expressed fact is ambiguous,
  conflicting, or unresolved—for example which of three “Research” tasks a relation targets.
- **Contains:** Item anchor, optional Event anchor, affected semantic field, reason, candidates when
  available, and a candidate question.
- **Does not mean:** every optional field that the user omitted. Missing duration is not
  automatically a Gap merely because a database model wants a number.
- **Consumed by:** Planner, which decides whether the uncertainty materially blocks planning and
  centrally emits clarification when required.

#### Residue

- **Means:** an exact source fragment that the current Event vocabulary or Interpreter version
  cannot safely express.
- **Contains:** Item anchor, source span/text, typed reason, optional capability hint, Interpreter
  version, and eventual handling outcome.
- **Consumed by:** Planner only to decide whether to continue with an explicit assumption, ask an
  Item-anchored clarification, or return a typed capability failure.
- **Must not:** be silently reinterpreted by Planner and written back as Event fact.
- **Recorded in:** the capability-gap registry for review, regression fixtures, and future Updater
  work.

If an Interpreter fallback is needed, it remains owned by Interpreter:

```text
Item -> Interpreter -> Interpreter fallback -> Event / Residue -> Planner
```

The forbidden alternative is `Residue -> Planner as a second Interpreter -> rewritten Event`.

#### Directive library

- **Represents:** a non-timeline conversational request such as query, explanation, replan request,
  information, or unknown input.
- **Produces:** a first-class Chronos Log response with optional clickable Timeline references.
- **Must not:** create an executable Operation solely to display a reply.

#### Estimator

- **Reads:** Monitor evidence, committed Timeline, Forecast, Profile, freshness, and confidence.
- **Produces:** State.
- **Owns:** interpretation of the user's changing condition, not interpretation of Prompt language.
- **Must not:** create Events, Plans, or Operations.

#### State library

- **Contains:** the smallest current user/schedule facts Planner needs, each with confidence,
  freshness, and source where relevant.
- **Examples:** current time, activity, focus/load estimate, interruptibility, active task,
  availability, and forecast risk.
- **Must not:** merge raw Monitor streams with Prompt semantics or pretend uncertain estimates are
  user-confirmed facts.

#### Planner

- **Reads:** Events, Directives that require planning, State, and a bounded Schedule view. It
  normally does not read Prompt.
- **Produces:** exactly one Plan snapshot containing resolved changes, concrete timing, conflicts,
  assumptions, explanations, horizon, and source Event references.
- **Owns:** constraint resolution, slot choice, prospective/historical horizon, relations, fixed
  constraints, planning objectives, and centralized post-Parser clarification decisions.
- **May inspect Residue only to choose:** explicit assumption, clarification, or typed failure.
- **Must not:** rewrite Event facts, redo general language interpretation, emit Runtime-specific
  payloads, or accept Operations as input.

#### Plan library

- **Is:** the single resolved planning truth between Events and Operations.
- **Contains:** concrete task/reminder drafts or changes, resolved timing and relations, conflicts,
  assumptions, explanation, planning horizon, source Event ids, and version/concurrency basis.
- **Feeds:** Proposal text, Timeline Projection, Lowerer, and Gate metadata.
- **Must not:** coexist with a proposal-only execution payload containing different changes.

#### Lowerer

- **Reads:** Plan only.
- **Produces:** the registered finite Operations union.
- **Owns:** deterministic conversion from resolved changes to Runtime primitives.
- **Must not:** read Prompt, call a model, choose slots, ask clarification, or raise Operations back
  into Events/Plan.

#### Operations library

- **Is:** the only executable representation accepted by Runtime.
- **Contains:** finite, registered, strictly validated primitives such as create/update/delete of
  Task or Reminder.
- **Must not:** contain symbolic time, unresolved semantics, model-invented operation names, or
  proposal-specific hidden payloads.

#### Gate

- **Reads:** Plan/Operation risk metadata, ambiguity, impact, reversibility, concurrency state, and
  Autonomy policy.
- **Produces:** propose, direct execute, or reject/clarify decision.
- **Must not:** change the Plan or create alternate Operations.

#### Runtime

- **Reads:** an approved AgentOperation and Operations only.
- **Owns:** final validation, prospective-past guard, optimistic concurrency, transaction, domain
  writes, rollback, Log lifecycle entries, and Undo snapshot.
- **Must not:** call Planner, understand Prompt/Event, reconstruct Tasks for replanning, or execute a
  legacy Proposal payload instead of Operations.

#### AgentOperation and views

- **AgentOperation:** lifecycle envelope for the current Items/Events/Plan/Operations snapshot,
  clarification state, version, risk, and status. It does not introduce another semantic model.
- **Proposal:** `AgentOperation.state = proposed` plus a confirmation view of the same Plan and
  Operations.
- **Projection:** spatial view derived from that same Plan/Operations; it remains visible when Log
  is folded.
- **Log:** append-only human-readable lifecycle and Directive replies, with references back to the
  same objects.
- Proposal, Projection, and Log must never become independent sources of executable truth.

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

### Migration status (2026-08-16)

Completed on the canonical path:

- Contracts for Parser, Items, Events, Gap, Residue, Directive, State, Plan, Lowerer, Operations,
  Runtime, and persisted AgentOperation snapshots.
- Exact-span parsing, versioned Interpreter snapshots, full semantic clarification continuation,
  and persistent Residue registry.
- Task creation for symbolic periods, combined Item content (`A · B`) with one total duration, and
  point/window/context-aware Reminder creation.
- `Plan -> Operations -> Runtime` task/reminder execution, prospective NOW guard, transactions,
  rollback, undo, Log events, and Proposal/Projection views derived from canonical state.
- Existing `/api/v1/proposals` create/clarify/accept entry points switch to the canonical Flow when
  a configured language model is available. Directive replies bypass Planner and appear in Log.
- Schedule-domain `Plan` was renamed `Agenda`; Agent `Plan` is now the only planning-result name.
- ProposalSnapshot no longer owns Operations. Runtime's former ProposalService execution methods
  are isolated as `execute_legacy` and `revert_legacy`.

Still to migrate before deleting compatibility code:

- Semantic and planning support for edit/delete, recurrence, selected object/range context,
  relative time, historical recording, and broad replanning.
- Gate/autonomy execution in canonical Flow; current migrated requests remain proposals.
- Canonical stale/recompile behavior and Plan-based projection for non-create changes.
- Removal of `LLMChronosCompiler`, `AgentInterpretation`, proposal Task reconstruction,
  ScheduleCommandBatch Agent routing, and ProposalService execution duties after parity tests pass.
- Rename legacy SQLite/wire `plans` and `plan_id` only through an explicit storage/API migration;
  they currently refer to Schedule Agenda compatibility data.

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
