# Chronos roadmap

This document is the single backlog for valuable capabilities that should be remembered but are
not part of the current implementation. Each item should state its current limitation, intended
outcome, and delivery checkpoints. Completed work should move to release notes or architecture
documentation instead of remaining here.

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

The Agent now loads a Git-ignored personal Markdown profile through a content-hash cache. It also
accepts GPT/Claude-generated Markdown profiles and ChatGPT/Claude export ZIPs, retains them locally,
parses them without model upload, deduplicates them, and presents reviewable candidates; accepted
items enter the semantic-provider context. The Agent does not yet retrieve only relevant items,
model contradictions, or synchronize directly from Codex, ChatGPT, Claude, Octopus, or other live
sources.

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

1. Add contradiction/change detection between import snapshots instead of content-hash deduplication
   alone, plus explicit deletion of accepted context items.
2. Expose a reverse-sync MCP/App tool so ChatGPT, Codex, or Claude can submit reviewed candidate
   memories to Chronos from inside an authenticated conversation without sharing account cookies.
3. Keep provider OAuth adapters behind a capability interface. Enable direct account sync only when
   a provider publishes an authorization scope and API for conversations or memories; ordinary
   “Sign in with ChatGPT” identity is not treated as data access.
4. Add relevance selection and a visible “context used” section to every proposal explanation.
5. Add opt-in adapters for Schedule, Monitor summaries, notes, calendar, Codex/ChatGPT exports, and
   Octopus imports.
6. Add conflict resolution, retention controls, redaction, and encrypted-at-rest options.

### Provider account constraints

- Do not scrape ChatGPT or Claude web interfaces, reuse browser cookies, automate MFA, or store
  consumer-session tokens.
- ChatGPT and Claude account exports are asynchronous user-controlled archives today, so archive
  import is a refresh workflow rather than continuous OAuth polling.
- Store a source cursor/hash and show only additions, changes, contradictions, and possible removals
  since the last accepted import. Never turn an imported chat directly into durable memory.
