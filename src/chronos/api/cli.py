"""Command-line entry points for connecting local platform agents."""

from __future__ import annotations

import argparse
import sys

from chronos.monitor.live import LiveRecognizer
from chronos.monitor.serialization import monitor_snapshot_to_json, observation_from_json
from chronos.monitor.snapshots import MonitorSnapshot, SnapshotAssembler, WorkStateSnapshot


def main() -> int:
    parser = argparse.ArgumentParser(prog="chronos")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("recognize", help="read Observation JSONL from stdin")
    args = parser.parse_args()

    if args.command == "recognize":
        return _recognize()
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


if __name__ == "__main__":
    raise SystemExit(main())
