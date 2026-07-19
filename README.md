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

## Architecture

```text
src/chronos/
├── collectors/       # Cross-platform collector protocol
├── observation/      # Bounded ingestion and lifecycle management
└── estimation/       # Feature aggregation, state estimation, segment building
```

The estimator runs locally with deterministic rules. An optional `SemanticInferenceProvider`
boundary allows an LLM or another model to enrich ambiguous activity and task semantics without
making basic presence detection depend on a network service.

## Development

The core currently has no runtime dependencies and requires Python 3.12 or later.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```
