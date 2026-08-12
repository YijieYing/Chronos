# Chronos architecture

Chronos starts as a modular monolith with three independent domain boundaries. This keeps local
deployment simple without allowing monitoring, scheduling, and automatic adaptation to collapse
into one model.

## Bounded contexts

### Monitor

Monitor answers what the user is doing now and what likely happened during a past interval.

It owns observations, collector health, feature windows, work-state estimates, activity segments,
multi-device presence, and monitor snapshots. It may publish events such as:

- `monitor.work_state_updated`
- `monitor.activity_segment_finalized`
- `monitor.collector_degraded`

Monitor does not know about schedule blocks or modify a plan.

### Schedule

Schedule answers what the user intends to do and when it can be done.

It owns tasks, goals, fixed and flexible blocks, constraints, plan versions, calendar projections,
and explicit schedule commands. It may publish events such as:

- `schedule.plan_created`
- `schedule.plan_revised`
- `schedule.block_changed`

Schedule can operate without Monitor. It does not infer actual behavior and does not automatically
react to work-state updates.

The HTTP timeline is a projection of Schedule tasks and activated plan blocks, not a separate
write model. The former `timeline_tasks` storage is a migration source only. Recurring occurrences
are projected by the backend planner over a bounded horizon and retain their series identity for
editing. User edits and Agent requests both become Schedule commands; Agent commands first produce
a persisted draft proposal and require explicit acceptance before changing tasks or plans.

Natural-language parsing is an input adapter behind `ScheduleCommandParser`. Semantic creation
requests pass through a source-grounded `AgentInterpretation`, clarification when required, a typed
`ScheduleCommandBatch`, 14-day planner preview, and atomic confirmation. Update, delete, and query requests
remain typed single commands. This keeps model interpretation separate from planner rules,
persistence, stale-plan checks, and confirmation policy.

Daily and weekly recurrence may include an inclusive `until` date. Interpretation provenance is
field-level and supports multiple exact source fragments, allowing one grammatical modifier to
ground multiple tasks while keeping every frequency, weekday, and end-date claim traceable to the
request.

The first Schedule prototype uses a deterministic daily planner over the full 24-hour day. It sorts
eligible tasks by priority, deadline, and creation time; subtracts fixed constraints; splits tasks
only when allowed; and records any remainder explicitly. Generated plans are immutable, versioned
drafts until the user activates one.

### Adaptation

Adaptation compares intent from Schedule with evidence from Monitor and decides whether intervention
is worthwhile.

It owns deviation detection, candidate adjustments, risk and authorization policy, prompt cooldowns,
user feedback, proposal explanations, and execution decisions. A proposal can be suggestion-only,
require confirmation, or be eligible for automatic execution. Notification delivery remains a port,
so policy is independent from macOS notifications or a future mobile UI.

Adaptation may consume Monitor and Schedule events and issue validated Schedule commands. Monitor and
Schedule never import Adaptation.

## Dependency direction

```text
Monitor events ─────┐
                    ├──> Adaptation ──> Schedule commands
Schedule events ────┘          │
                               └──> Prompt/notification port
```

An in-process event dispatcher is sufficient initially. Domain event contracts should remain plain,
versioned data so they can later cross a process boundary without redesigning the domains.

## Target source layout

```text
src/chronos/
├── monitor/
│   ├── models.py
│   ├── observations.py
│   ├── aggregation.py
│   ├── estimator.py
│   ├── segments.py
│   ├── snapshots.py
│   ├── live.py
│   ├── serialization.py
│   ├── events.py
│   └── ports.py
├── schedule/
│   ├── models.py
│   ├── constraints.py
│   ├── planner.py
│   ├── commands.py
│   ├── events.py
│   └── ports.py
├── adaptation/
│   ├── models.py
│   ├── deviation.py
│   ├── reconciler.py
│   ├── policy.py
│   ├── prompts.py
│   └── ports.py
├── infrastructure/
│   ├── persistence/
│   ├── event_bus.py
│   └── integrations/
└── api/
    ├── cli.py
    └── http.py
```

The native macOS collector remains under `apps/mac-agent`; it is an external adapter that speaks the
Monitor Observation contract, not part of the Schedule domain.

The thin desktop shell under `apps/mac-app` hosts the React frontend in `WKWebView`. It owns window
behavior, navigation policy, resource loading, and the native bridge, but no Monitor or Schedule
business logic.

The initial package does not need every file in this target tree. Directories should appear when the
corresponding behavior exists. Avoid a generic `shared` domain folder: identifiers, clocks, and event
envelopes may be shared infrastructure, but tasks, work states, and adjustment proposals retain a
single owning context.

## Monitor output

Collectors continue to publish independent Observations at their natural cadence. A materialized
snapshot provides a convenient external view without merging collector implementations:

```text
independent Observations
        -> SnapshotAssembler
        -> DeviceObservationSnapshot
        -> independent inference modules
        -> WorkStateSnapshot
        -> MonitorSnapshot
```

Every module reports its own status, timestamp, schema version, data, and—when applicable—confidence
and model version. Consumers ignore unknown modules, allowing location, project activity, calendar
context, and remote-device presence to be added compatibly.
