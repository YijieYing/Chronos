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

## Schedule prototype

Schedule runs independently from Monitor across the full 24-hour day. It includes task intake,
fixed-task constraints, deterministic planning, explicit unscheduled remainders, plan versions, and
draft activation. State is persisted locally in SQLite.

The UI is a React/TypeScript temporal field rendered with SVG and animated with Framer Motion.
Timeline tasks are persisted through the local service in the same SQLite database as Schedule and
Monitor state. A recurring task is stored once as a series rule; the frontend expands only the
visible occurrences, so daily tasks do not create an ever-growing set of future database rows.

Monitor samples pass through a frontend adapter that produces current cognitive state, efficiency,
predicted completion delay, and a six-hour `cognitive_load` / `mental_fatigue` forecast. When the
native Monitor is unavailable, the UI explicitly labels its synthetic fallback as `DEMO DATA`. Raw
observations are not displayed.

Start the local server:

```bash
npm install --prefix web
npm --prefix web run build
./scripts/run-schedule.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The default database is
`data/chronos.sqlite3`; this directory is ignored by Git.

For UI development with hot reload, run the API and Vite separately:

```bash
./scripts/run-schedule.sh
npm --prefix web run dev
```

The server binds to localhost by default and serves the production build from `web/dist/`.

## macOS app shell

The lightweight macOS shell in `apps/mac-app/` hosts the same React interface in `WKWebView`.
It disables WebKit back/forward trackpad navigation, keeps external links outside the app, and
exposes a deliberately small `window.chronosNative` bridge for future Monitor integration.

Build the shell and bundle the current frontend:

```bash
./scripts/build-mac-app.sh
```

Run it:

```bash
./scripts/run-mac-app.sh
```

This single command starts the local Schedule service when needed, opens and activates the macOS
window, and starts the native macOS Monitor when no live collector is already connected. It stops
the service and collector processes it started when the app exits. If a Schedule service or Monitor
is already running, the shell reuses it. Set `CHRONOS_WEB_URL` to another localhost URL, such as
Vite on port 5173, for development.

The local service accepts normalized Observation objects at
`POST /api/monitor/observations`. It exposes interpreted state—not raw keyboard or window events—at:

```text
GET /api/current-state
GET /api/cognitive-state?from=<epoch-ms>&to=<epoch-ms>
```

Timeline persistence is exposed at:

```text
GET    /api/timeline/tasks
POST   /api/timeline/tasks
PUT    /api/timeline/tasks/<id>
DELETE /api/timeline/tasks/<id>
```

The versioned Schedule API is the canonical frontend boundary. New writes no longer target the
legacy `timeline_tasks` table; those rows are imported once into Schedule tasks when the local
service starts. Every task mutation produces and activates a new planner version.

```text
GET    /api/v1/schedule/timeline
POST   /api/v1/schedule/tasks
PUT    /api/v1/schedule/tasks/<id>
DELETE /api/v1/schedule/tasks/<id>

GET    /api/v1/proposals
POST   /api/v1/proposals
POST   /api/v1/proposals/<id>/accept
POST   /api/v1/proposals/<id>/reject
POST   /api/v1/proposals/<id>/restore
```

V1 responses use a stable envelope with `schema_version`, `request_id`, `data`, and `error`.
Agent requests are parsed into structured Schedule commands on the backend. The built-in parser
supports create, move/resize, delete, and query requests behind a replaceable parser interface.
Mutating commands are previewed by the same Schedule planner and persisted as explainable proposals;
tasks change only after acceptance, and accepted changes can be restored. Proposal state and
explanations survive UI reloads.

Agent providers are selected through `config/agent.local.toml` (ignored by Git). The checked-in
`config/agent.example.toml` contains presets for DeepSeek, OpenAI/OpenAI-compatible APIs, Anthropic,
and Gemini. DeepSeek is selected by default; with an empty key, Chronos safely uses the local
deterministic parser. Restart the local service after changing provider configuration. Override the
path with `CHRONOS_AGENT_CONFIG` or `--agent-config`.

Stable personal context lives in `config/agent.local.md`, which is also ignored by Git. Chronos
caches its parsed content and checks only the filesystem fingerprint on each Agent request; it
re-reads and re-hashes the file only after a change. For semantic providers, the profile is placed
in the stable system-prompt prefix so provider-side prefix caching can reuse it. Keep this file
concise and use Chronos data sources for frequently changing state. The maximum size is controlled
by `agent.profile_max_chars` in the TOML configuration.

### Personal context imports

The fastest path is to ask ChatGPT or Claude to describe you as structured Markdown. Open
**MEMORY SYNC**, click **COPY GPT PROMPT**, send that prompt in a conversation that knows you,
save the response as a UTF-8 `.md` file, then drag it into Chronos. A normal Markdown document also
works as long as personal facts are separate bullet or numbered-list items under descriptive
headings. Sections may be added, removed, or renamed; unknown headings use the generic `personal`
category. Nested lists retain their parent context, and a whole document wrapped in a
`markdown` code fence is accepted. The checked-in template is
`config/personal-profile-import.example.md`.

For deeper history, the same drop zone still accepts ChatGPT or Claude account-export ZIP files.
Select the source before importing. Chronos retains every original private Markdown, text, or ZIP
document under the following Git-ignored folders:

```text
data/agent-imports/chatgpt/
data/agent-imports/claude/
```

In this workspace those resolve to:

```text
/Users/prts/Projects/Chronos/data/agent-imports/chatgpt/
/Users/prts/Projects/Chronos/data/agent-imports/claude/
```

Change the root with `--agent-import-dir`. Uploads are limited to 50 MB; ZIP content is limited to
200 MB uncompressed. Chronos parses Markdown headings and list items locally. For ZIPs, it reads
`conversations.json` directly without extracting it and uses local rules to propose personal
statements. Imported source files are never sent to the configured model provider. Re-importing an
identical file is a no-op. Accepted candidates are stored in SQLite and included in subsequent
semantic Agent requests; ignored candidates remain auditable but are not used as context.

```text
POST /api/v1/agent/imports?source=chatgpt|claude&filename=<name.md|name.zip>
GET  /api/v1/agent/imports
GET  /api/v1/agent/memory/candidates
POST /api/v1/agent/memory/candidates/<id>/accept
POST /api/v1/agent/memory/candidates/<id>/ignore
GET  /api/v1/agent/memory/items
```

Background collection is not yet independent of the desktop window. The current behavior and the
Octopus-inspired service roadmap are recorded in
[`docs/roadmap.md`](docs/roadmap.md).

The current five-minute bucket is updated in place in SQLite. The UI reads at most 288 points from
the past 24 hours. If no Monitor observation has arrived recently, the frontend explicitly switches
to `DEMO DATA`.
