"""Command-line entry points for connecting local platform agents."""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from urllib.parse import urlparse

from chronos.monitor.live import LiveRecognizer
from chronos.monitor.serialization import monitor_snapshot_to_json, observation_from_json
from chronos.monitor.snapshots import MonitorSnapshot, SnapshotAssembler, WorkStateSnapshot


def main() -> int:
    parser = argparse.ArgumentParser(prog="chronos")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("recognize", help="read Observation JSONL from stdin")
    forward = subcommands.add_parser("forward", help="forward Observation JSONL to Chronos")
    forward.add_argument(
        "--endpoint",
        default="http://127.0.0.1:8765/api/monitor/observations",
    )
    args = parser.parse_args()

    if args.command == "recognize":
        return _recognize()
    if args.command == "forward":
        return _forward(args.endpoint)
    return 2


def _recognize() -> int:
    recognizer = LiveRecognizer()
    snapshots = SnapshotAssembler()
    try:
        for line_number, line in enumerate(sys.stdin, start=1):
            if not line.strip():
                continue
            try:
                observation = observation_from_json(line)
                snapshots.ingest(observation)
                estimate = recognizer.ingest(observation)
            except (KeyError, TypeError, ValueError) as error:
                print(f"invalid observation on line {line_number}: {error}", file=sys.stderr)
                continue
            if estimate is not None:
                generated_at = estimate.evaluated_at
                snapshot = MonitorSnapshot(
                    device_id=estimate.device_id,
                    generated_at=generated_at,
                    observations=snapshots.snapshot(
                        estimate.device_id,
                        generated_at=generated_at,
                    ),
                    work_state=WorkStateSnapshot.from_estimate(estimate),
                )
                print(monitor_snapshot_to_json(snapshot), flush=True)
    except KeyboardInterrupt:
        return 0
    return 0


def _forward(endpoint: str) -> int:
    target = urlparse(endpoint)
    if target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("monitor endpoint must be local HTTP")
    path = target.path or "/api/monitor/observations"
    if target.query:
        path += f"?{target.query}"
    connection: http.client.HTTPConnection | None = None
    try:
        for line_number, line in enumerate(sys.stdin, start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
                if connection is None:
                    connection = http.client.HTTPConnection(
                        target.hostname,
                        target.port or 80,
                        timeout=5,
                    )
                connection.request(
                    "POST",
                    path,
                    body=line.encode(),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                response.read()
                if response.status >= 400:
                    print(
                        f"monitor ingest rejected line {line_number}: HTTP {response.status}",
                        file=sys.stderr,
                    )
            except (OSError, ValueError, json.JSONDecodeError) as error:
                print(f"monitor forward failed on line {line_number}: {error}", file=sys.stderr)
                if connection is not None:
                    connection.close()
                    connection = None
    except KeyboardInterrupt:
        return 0
    finally:
        if connection is not None:
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
