"""Dependency-free local HTTP server for the Schedule prototype."""

from __future__ import annotations

import argparse
import json
import mimetypes
import traceback
from datetime import UTC, date, datetime, time, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from chronos.api.contracts.common import failure, success
from chronos.api.contracts.schedule import scheduled_task_values
from chronos.api.routes.v1 import V1Router
from chronos.infrastructure.sqlite_agent_memory import SQLiteAgentMemoryRepository
from chronos.infrastructure.sqlite_cognitive_state import SQLiteCognitiveStateRepository
from chronos.infrastructure.sqlite_proposals import SQLiteProposalRepository
from chronos.infrastructure.sqlite_schedule import SQLiteScheduleRepository
from chronos.infrastructure.sqlite_timeline import SQLiteTimelineRepository
from chronos.monitor.cognitive import cognitive_point_dict
from chronos.monitor.serialization import observation_from_json
from chronos.monitor.service import MonitorService
from chronos.schedule.agent_config import load_agent_config
from chronos.schedule.agent_memory import MAX_ARCHIVE_BYTES, AgentMemoryService
from chronos.schedule.models import TaskStatus
from chronos.schedule.proposals import ProposalService
from chronos.schedule.semantic_parser import build_command_parser
from chronos.schedule.service import ScheduleService


class ScheduleRequestHandler(BaseHTTPRequestHandler):
    service: ScheduleService
    monitor_service: MonitorService
    timeline_repository: SQLiteTimelineRepository
    v1_router: V1Router
    web_root: Path
    agent_provider: str
    agent_semantic_enabled: bool
    agent_memory_service: AgentMemoryService

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if self._dispatch_v1("GET", parsed.path):
            return
        if parsed.path == "/api/health":
            self._json(
                {
                    "service": "chronos-local",
                    "schema_version": 2,
                    "capabilities": [
                        "schedule",
                        "monitor-observation-ingest",
                        "cognitive-state-history",
                        "timeline-task-storage",
                        "schedule-v1",
                        "schedule-proposals",
                    ],
                    "agent": {
                        "provider": self.agent_provider,
                        "semantic_enabled": self.agent_semantic_enabled,
                    },
                }
            )
            return
        if parsed.path == "/api/schedule":
            query = parse_qs(parsed.query)
            target = date.fromisoformat(query.get("date", [date.today().isoformat()])[0])
            self._json(self.service.snapshot(target))
            return
        if parsed.path == "/api/timeline/tasks":
            self._json(self.service.timeline())
            return
        if parsed.path == "/api/current-state":
            self._json(self.monitor_service.current())
            return
        if parsed.path == "/api/cognitive-state":
            query = parse_qs(parsed.query)
            now = datetime.now(UTC)
            start = _query_datetime(query.get("from", [None])[0], now - timedelta(hours=24))
            end = _query_datetime(query.get("to", [None])[0], now)
            self._json(self.monitor_service.history(start, end))
            return
        self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/v1/agent/imports":
                query = parse_qs(parsed.query)
                imported = self.agent_memory_service.import_document(
                    source=query.get("source", [""])[0],
                    filename=query.get("filename", ["export.zip"])[0],
                    data=self._binary_body(MAX_ARCHIVE_BYTES),
                )
                self._json(success(imported), HTTPStatus.CREATED)
                return
            payload = self._body()
            if self._dispatch_v1("POST", parsed.path, payload):
                return
            if self.path == "/api/monitor/observations":
                observation = observation_from_json(json.dumps(payload))
                point = self.monitor_service.ingest(observation)
                self._json(
                    {"accepted": True, "point": cognitive_point_dict(point) if point else None},
                    HTTPStatus.ACCEPTED,
                )
                return
            if self.path == "/api/tasks":
                settings = self.service.settings()
                deadline = _optional_datetime(payload.get("deadline"), settings["timezone"])
                self.service.create_task(
                    title=str(payload["title"]),
                    estimated_minutes=int(payload["estimated_minutes"]),
                    priority=int(payload.get("priority", 3)),
                    deadline=deadline,
                    splittable=bool(payload.get("splittable", True)),
                    min_chunk_minutes=int(payload.get("min_chunk_minutes", 25)),
                )
                self._json({"ok": True}, HTTPStatus.CREATED)
                return
            if self.path == "/api/timeline/tasks":
                values = scheduled_task_values(
                    payload,
                    self.service.settings()["timezone"],
                    task_id=str(payload["id"]),
                )
                task, _ = self.service.create_scheduled_task(**values)
                projected = next(
                    item
                    for item in self.service.timeline()["tasks"]
                    if item["id"] == task.task_id
                )
                self._json({"task": projected}, HTTPStatus.CREATED)
                return
            if self.path == "/api/fixed-blocks":
                self.service.create_fixed_block(
                    title=str(payload["title"]),
                    target_date=date.fromisoformat(str(payload["date"])),
                    start_time=time.fromisoformat(str(payload["start_time"])),
                    end_time=time.fromisoformat(str(payload["end_time"])),
                )
                self._json({"ok": True}, HTTPStatus.CREATED)
                return
            if self.path == "/api/plans/generate":
                plan = self.service.generate_plan(date.fromisoformat(str(payload["date"])))
                self._json({"ok": True, "plan_id": plan.plan_id}, HTTPStatus.CREATED)
                return
            if self.path.startswith("/api/plans/") and self.path.endswith("/activate"):
                plan_id = self.path.removeprefix("/api/plans/").removesuffix("/activate")
                self.service.activate_plan(plan_id)
                self._json({"ok": True})
                return
            self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
        except KeyError as error:
            self._error(HTTPStatus.NOT_FOUND, f"not found: {error.args[0]}")
        except (TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except RuntimeError as error:
            print(
                f"Agent request failed: {type(error).__name__}: {error}",
                flush=True,
            )
            self._error(HTTPStatus.BAD_GATEWAY, str(error))
        except Exception:
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")

    def do_PATCH(self) -> None:  # noqa: N802
        try:
            if not self.path.startswith("/api/tasks/"):
                self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
                return
            task_id = self.path.removeprefix("/api/tasks/")
            payload = self._body()
            self.service.set_task_status(task_id, TaskStatus(str(payload["status"])))
            self._json({"ok": True})
        except KeyError as error:
            self._error(HTTPStatus.NOT_FOUND, f"not found: {error.args[0]}")
        except (TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except RuntimeError as error:
            print(
                f"Agent request failed: {type(error).__name__}: {error}",
                flush=True,
            )
            self._error(HTTPStatus.BAD_GATEWAY, str(error))
        except Exception:
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")

    def do_PUT(self) -> None:  # noqa: N802
        try:
            payload = self._body()
            if self._dispatch_v1("PUT", urlparse(self.path).path, payload):
                return
            if self.path.startswith("/api/timeline/tasks/"):
                task_id = self.path.removeprefix("/api/timeline/tasks/")
                if str(payload.get("id", "")) != task_id:
                    raise ValueError("timeline task id must match request path")
                values = scheduled_task_values(
                    payload, self.service.settings()["timezone"]
                )
                task, _ = self.service.update_scheduled_task(task_id, **values)
                projected = next(
                    item
                    for item in self.service.timeline()["tasks"]
                    if item["id"] == task.task_id
                )
                self._json({"task": projected})
                return
            if self.path != "/api/settings":
                self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
                return
            self._json(self.service.update_settings(payload))
        except (TypeError, ValueError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def do_DELETE(self) -> None:  # noqa: N802
        if self._dispatch_v1("DELETE", urlparse(self.path).path):
            return
        if self.path.startswith("/api/timeline/tasks/"):
            deleted = self.service.delete_scheduled_task(
                self.path.removeprefix("/api/timeline/tasks/")
            )
        elif self.path.startswith("/api/tasks/"):
            deleted = self.service.delete_task(self.path.removeprefix("/api/tasks/"))
        elif self.path.startswith("/api/fixed-blocks/"):
            deleted = self.service.delete_fixed_block(
                self.path.removeprefix("/api/fixed-blocks/")
            )
        else:
            self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
            return
        if not deleted:
            self._error(HTTPStatus.NOT_FOUND, "resource not found")
            return
        self._json({"ok": True})

    def log_message(self, format: str, *args: object) -> None:
        print(f"schedule | {self.address_string()} | {format % args}")

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _binary_body(self, maximum: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > maximum:
            raise ValueError(f"request body must be between 1 and {maximum} bytes")
        return self.rfile.read(length)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, message: str) -> None:
        if urlparse(self.path).path.startswith("/api/v1/"):
            code = {
                HTTPStatus.NOT_FOUND: "not_found",
                HTTPStatus.BAD_GATEWAY: "upstream_error",
                HTTPStatus.INTERNAL_SERVER_ERROR: "internal_error",
            }.get(status, "invalid_request")
            self._json(failure(code, message), status)
            return
        self._json({"error": message}, status)

    def _dispatch_v1(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> bool:
        if not path.startswith("/api/v1/"):
            return False
        try:
            result = self.v1_router.dispatch(method, path, payload)
            assert result is not None
            status, response = result
            self._json(response, status)
        except KeyError as error:
            target = error.args[0] if error.args else path
            self._error(HTTPStatus.NOT_FOUND, f"not found: {target}")
        except (TypeError, ValueError) as error:
            if path == "/api/v1/proposals":
                print(
                    f"Agent request rejected: {type(error).__name__}: {error}",
                    flush=True,
                )
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except RuntimeError as error:
            print(
                f"Agent request failed: {type(error).__name__}: {error}",
                flush=True,
            )
            self._error(HTTPStatus.BAD_GATEWAY, str(error))
        except Exception:
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
        return True

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (self.web_root / relative).resolve()
        resolved_root = self.web_root.resolve()
        if resolved_root not in candidate.parents and candidate != resolved_root:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not candidate.is_file():
            candidate = self.web_root / "index.html"
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Chronos Schedule prototype")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", default="data/chronos.sqlite3")
    parser.add_argument(
        "--agent-config",
        help="Agent TOML path (default: CHRONOS_AGENT_CONFIG or config/agent.local.toml)",
    )
    parser.add_argument(
        "--agent-import-dir",
        default="data/agent-imports",
        help="private directory used to retain uploaded profile documents and account exports",
    )
    args = parser.parse_args()

    repository = SQLiteScheduleRepository(args.database)
    service = ScheduleService(repository)
    cognitive_repository = SQLiteCognitiveStateRepository(args.database)
    monitor_service = MonitorService(cognitive_repository)
    timeline_repository = SQLiteTimelineRepository(args.database)
    service.import_legacy_timeline_tasks(timeline_repository.list_tasks())
    proposal_repository = SQLiteProposalRepository(args.database)
    memory_repository = SQLiteAgentMemoryRepository(args.database)
    memory_service = AgentMemoryService(memory_repository, args.agent_import_dir)
    agent_config = load_agent_config(args.agent_config)
    selected_provider = agent_config.selected_provider()
    semantic_enabled = bool(
        selected_provider
        and selected_provider.api_key
        and selected_provider.model
        and selected_provider.base_url
    )
    command_parser = build_command_parser(agent_config, memory_service.retrieve_context)
    proposal_service = ProposalService(service, proposal_repository, command_parser)
    v1_router = V1Router(service, proposal_service, memory_service)
    root = Path(__file__).resolve().parents[3] / "web" / "dist"
    if not root.is_dir():
        raise SystemExit("Frontend build not found. Run: npm --prefix web run build")
    handler = type(
        "ConfiguredScheduleRequestHandler",
        (ScheduleRequestHandler,),
        {
            "service": service,
            "monitor_service": monitor_service,
            "timeline_repository": timeline_repository,
            "v1_router": v1_router,
            "web_root": root,
            "agent_provider": agent_config.provider,
            "agent_semantic_enabled": semantic_enabled,
            "agent_memory_service": memory_service,
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Chronos Schedule: http://{args.host}:{args.port}")
    print(f"Database: {Path(args.database).resolve()}")
    print(
        f"Agent: {agent_config.provider} "
        f"({'semantic' if semantic_enabled else 'deterministic fallback'})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _optional_datetime(value: object, timezone: str) -> datetime | None:
    if value in {None, ""}:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _query_datetime(value: str | None, default: datetime) -> datetime:
    if value is None or value == "":
        return default
    try:
        parsed = datetime.fromtimestamp(int(value) / 1000, UTC)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


if __name__ == "__main__":
    raise SystemExit(main())
