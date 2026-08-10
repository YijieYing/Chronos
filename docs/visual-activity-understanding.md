# Visual activity understanding

## Goal and boundary

Visual understanding should improve Monitor's estimate of current activity and possible Schedule
task without turning Chronos into a screen-recording archive. Capture, privacy filtering, model
inference, and state fusion remain separate modules. Schedule never receives screenshots; it may
consume a stabilized Monitor activity segment only through a future Adaptation policy.

## Proposed pipeline

```text
platform capture
  -> change/rate gate
  -> local OCR + sensitive-region redaction
  -> short-lived frame buffer
  -> provider-neutral vision inference
  -> validated visual.activity evidence
  -> Monitor feature-window fusion
  -> WorkStateEstimate / ActivitySegment
```

1. A device collector samples the focused window or user-selected app, never every video frame.
2. Foreground changes, meaningful perceptual-hash changes, and low-confidence Monitor states trigger
   analysis. A hard rate and cost budget prevents accidental continuous API use.
3. Local OCR and application rules exclude password managers, private browsing, authentication,
   banking, notifications, and configured applications. Matching regions are blurred before any
   external request.
4. The vision adapter returns strict JSON. Raw provider prose is not an Observation.
5. The collector deletes the source image after validation. A raw-image history is off by default;
   an optional encrypted diagnostic ring buffer must have a short TTL and a visible purge control.
6. Monitor fuses visual evidence with foreground application, input aggregates, presence, and time.
   One screenshot cannot directly create durable history or alter Schedule.

## Evidence contract

Add `visual.activity` as evidence with a versioned payload similar to:

```json
{
  "schema_version": 1,
  "frame_hash": "sha256:...",
  "app_id": "com.example.app",
  "activity": "coding",
  "task_hint": "Chronos memory retrieval",
  "ui_state": "editing source code",
  "confidence": 0.82,
  "sensitive_content_detected": false,
  "model": {"provider": "configured", "name": "configured"},
  "captured_at": "...",
  "expires_at": "..."
}
```

The payload should contain a short task hint, not OCR text or a screenshot URL. Store the frame hash
for deduplication and audit, but do not make it a stable cross-device identifier.

## Platform adapters

### macOS

Use ScreenCaptureKit in `apps/mac-agent` with explicit Screen Recording permission. Prefer a focused
window or user-selected application filter, exclude Chronos itself, downscale before analysis, and
combine the result with the existing foreground and input collectors.

### Android

Use a companion app and MediaProjection. Capture requires an explicit user-approved projection
session and a foreground indicator. Prefer Android's single-app sharing where available. The mobile
collector sends structured evidence over an authenticated local/Tailscale channel, not raw images
unless external inference is explicitly enabled.

### iPhone and iPad

Do not promise invisible continuous background screenshots. Start with Device Activity aggregates
for authorized app/website usage plus a Share Extension for user-selected screenshots. A
user-initiated screen-capture session can be an optional foreground mode on supported OS versions.

## Delivery phases

1. Define `VisionAnalysisProvider`, the strict evidence contract, cost/rate policy, exclusion rules,
   and a replayable labeled evaluation set.
2. Add the macOS focused-window collector, local OCR/redaction, perceptual-hash gating, and a mock
   provider. Keep external calls disabled by default.
3. Connect the configured vision API, add an audit view showing capture reason and fields sent, and
   measure precision against foreground/input-only estimates.
4. Fuse visual evidence into Monitor with confidence decay and cross-device active-device
   arbitration. Only then allow ActivitySegment output.
5. Build Android capture and iOS metadata/manual-share companions after the desktop privacy and
   accuracy gates are proven.
