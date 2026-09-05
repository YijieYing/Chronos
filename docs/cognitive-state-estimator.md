# Cognitive State Estimator

Implementation status, 2026-09-05: this is a design specification, not an acceptance report.
The Python rule estimator, five-minute SQLite points and frontend current/history consumption exist.
The complete late-evidence/two-bucket revision contract, durable hourly/daily summaries, production
multi-device/user identity model, shared backend Forecast and personal calibration are not all implemented.
Treat those clauses below as targets. See the [feature audit](feature-audit-2026-09-05.md) and
[P3 roadmap](roadmap.md) for current scope and sequencing.

## Responsibility

The Cognitive State Estimator is the boundary between Monitor evidence and every consumer that
needs a human-state interpretation.

```text
Monitor Observations
        ↓
five-minute evidence window
        ↓
Cognitive State Estimator
        ↓
observed CognitiveStatePoint
        ├── Cognitive Load Track
        ├── User State Model
        └── Forecast Estimator
```

The frontend never reconstructs cognitive state from keyboard counts, foreground applications, or
raw focus estimates. The TypeScript implementation is a UI reference; the production estimator
belongs in the local Python Monitor service.

## Time model

One point represents one wall-clock-aligned five-minute bucket:

```text
14:00:00 ≤ evidence < 14:05:00 → point at 14:00:00
```

The service owns a single UTC clock. Local timezone is used only for recurrence and presentation.
NOW, history queries, and Forecast generation receive the same clock value.

A bucket is finalized when either:

- the next bucket begins; or
- ten seconds of allowed lateness has elapsed.

Evidence arriving after finalization may revise the most recent two buckets. Older late evidence is
ignored for the live track and counted in diagnostics. A revision increments the point revision but
does not append a duplicate.

## Input summary

The five-minute window contains aggregates, never raw input events:

```ts
interface CognitiveEvidenceWindow {
  start: number
  end: number

  workingFraction: number
  inferredTaskType?: string
  taskIntensity: number
  focus: number
  taskSwitchCount: number

  activityConfidence: number
  monitorCoverage: number
  activeDeviceCount: number
}
```

When multiple devices report activity, Chronos selects the most recent high-confidence active
device as the primary context. It does not add keyboard activity from devices together. A device
change contributes one context switch.

## Stateful variables

The estimator carries these values between buckets:

```ts
interface CognitiveEstimatorState {
  mentalFatigue: number
  continuousWorkMinutes: number
  recoveryMinutes: number
  switchingPressure: number
  lastObservedAt: number
}
```

After restart, the service restores the newest persisted point and its state metadata. If no recent
point exists, it starts with low fatigue and low confidence.

## Version 1 rules

All values are clamped to `0…1`.

### Continuous work

```text
working bucket:
    continuousWorkMinutes += observed minutes
    recoveryMinutes = 0

recovery bucket:
    continuousWorkMinutes -= 10 minutes
    recoveryMinutes += observed minutes
```

Continuous pressure uses a smooth curve:

```text
0 before 20 minutes
gradually rises between 20 and 120 minutes
1 after 120 minutes
```

### Switching pressure

Switching pressure is an exponentially decaying state:

```text
switchingPressure(t)
    = switchingPressure(t-1) × 0.72
    + normalizedSwitchCount(t) × 0.28
```

This makes a burst of switching visible for several buckets without permanently raising load.

### Mental fatigue

During work:

```text
fatigueGain
    = 0.004
    + taskIntensity × 0.009
    + continuousWorkPressure × 0.006
```

During recovery:

```text
recoveryRate
    = 0.014
    + recoveryDurationFactor × 0.016

mentalFatigue(t)
    = mentalFatigue(t-1)
    - recoveryRate × observationConfidence
```

Missing Monitor coverage is not treated as recovery. A long evidence gap only applies a small,
bounded decay and lowers confidence.

### Cognitive load

```text
cognitiveLoad
    = taskIntensity × 0.52
    + continuousWorkPressure × 0.18
    + switchingPressure × 0.15
    + mentalFatigue × 0.15
    - recoveryEffect
```

Task intensity describes current demand. Mental fatigue describes accumulated state. They remain
separate output fields even though fatigue has a small influence on experienced load.

Focus is also preserved separately. High cognitive load is not interpreted as low focus or poor
performance.

### Confidence

State confidence combines:

- Monitor coverage;
- activity-recognition confidence;
- temporal continuity;
- agreement between collectors and devices.

Low confidence changes how strongly the point influences Forecast and learning. It does not force
the load value to zero.

## Output

```ts
interface CognitiveStatePoint {
  time: number
  cognitiveLoad: number
  mentalFatigue: number
  focus: number
  taskType?: string
  taskConfidence: number
  recoveryState: "working" | "recovering" | "rested" | "unknown"
  source: "observed" | "predicted"
  modelVersion: string
  revision: number
}
```

The history repository stores observed points only. Predicted points are Forecast products and are
regenerated from the latest observed state.

## Persistence and retention

SQLite uses a unique key on `(user_id, bucket_start)` and upserts revisions:

```text
cognitive_state_points
├── user_id
├── bucket_start
├── cognitive_load
├── mental_fatigue
├── focus
├── task_type
├── task_confidence
├── recovery_state
├── model_version
└── revision
```

The live API returns at most the latest 288 points:

```http
GET /api/cognitive-state?from=...&to=...
```

Recommended retention:

- five-minute points: 48 hours locally;
- hourly state summaries: 30 days;
- daily summaries: retained for long-term pattern learning;
- raw Monitor observations: existing bounded TTL only.

The 24-hour UI record therefore has a hard data and memory bound.

## Forecast handoff

Forecast generation receives:

- the latest observed CognitiveStatePoint;
- recent observed state trend;
- planned Schedule blocks;
- personal calibration parameters;
- one shared NOW timestamp.

It produces predicted state and PredictionRecords, but never writes predicted points into observed
history. Every refresh replaces the previous forecast for the same schedule/model version.

The workload track consequently has a strict semantic boundary:

```text
left of NOW  = persisted observed points
right of NOW = replaceable predicted points
```

## Evaluation

Estimator evaluation measures:

- continuity under normal sampling;
- recovery and fatigue response;
- confidence calibration;
- stability under missing evidence;
- agreement with explicit user corrections.

It does not measure productivity. Forecast evaluation separately measures duration error and
prediction-interval coverage through PredictionRecord and TaskOutcome.
