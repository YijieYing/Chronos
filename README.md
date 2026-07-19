# Chronos

Chronos is a local-first work-state estimation and dynamic planning engine. It observes what a
person is doing now, stabilizes those observations into activity history, and will use that history
to revise future plans.

Chronos is independent from Daytrace. A future integration may export finalized activity segments
to Daytrace as one of its information sources.

## Current scope

The first vertical slice focuses on recognizing current activity:

```text
platform signals
    -> Observation
    -> FeatureWindow
    -> WorkStateEstimate
    -> ActivitySegment
```

- `Observation` is normalized evidence without behavioral interpretation.
- `FeatureWindow` is a deterministic, bounded aggregation of recent evidence.
- `WorkStateEstimate` is a real-time hypothesis that later evidence may revise.
- `ActivitySegment` is a stabilized interval suitable for durable history.

Raw keyboard and pointer events are expected to be aggregated inside platform agents. Chronos does
not store key contents or an unbounded event stream.

The live recognizer keeps at most 2,000 observations and expires observations older than 10 minutes.
TTL cleanup runs whenever a new observation arrives; capacity remains a hard bound even while idle.

## Architecture

```text
src/chronos/
├── monitor/          # What the user is doing now and what happened
├── schedule/         # Tasks, constraints, plans, and calendar operations
├── adaptation/       # Reconciliation, proposals, prompts, and automation policy
├── infrastructure/   # Persistence, event delivery, clocks, and external adapters
└── api/              # CLI and future local HTTP/UI boundaries

apps/mac-agent/        # Native macOS observation agent
```

Chronos is a modular monolith. Monitor and Schedule are independent bounded contexts. Adaptation
connects them through published events and explicit commands; neither Monitor nor Schedule depends
on Adaptation. See [docs/architecture.md](docs/architecture.md).

The estimator runs locally with deterministic rules. An optional `SemanticInferenceProvider`
boundary allows an LLM or another model to enrich ambiguous activity and task semantics without
making basic presence detection depend on a network service.

## Development

The core currently has no runtime dependencies and requires Python 3.12 or later.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## macOS live loop

The first native agent implements three collectors:

- input activity: aggregated keyboard, click, pointer, scroll, and idle metrics;
- foreground context: frontmost application plus the focused window title when permitted;
- session state: sleep, wake, screen sleep/wake, and active-session changes.

Build it with:

```bash
swift build --package-path apps/mac-agent
```

Run the agent and pipe its Observation JSONL stream into the recognizer:

```bash
./scripts/run-mac-loop.sh --device-id my-macbook
```

The recognizer prints a versioned `chronos.monitor_snapshot` after input windows and important
session changes. Each snapshot contains independent collector modules under `observations.modules`
and independent inference modules under `work_state.modules`. Stop it with `Ctrl-C`.

### Permissions

The agent requests only the permissions needed by an enabled collector:

- **Input Monitoring** enables global keyboard and pointer event counts. Chronos never records key
  contents or full pointer trails.
- **Accessibility** enables focused-window titles. Without it, foreground application identity is
  still collected.

Grant access in **System Settings → Privacy & Security**, then restart the agent. If a permission is
denied, that collector degrades independently and the rest of the live loop continues running.

Screen Recording is not used by this version.
