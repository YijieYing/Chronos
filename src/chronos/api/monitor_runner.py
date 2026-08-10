"""Run the native macOS agent and forward its JSONL stream to the local Chronos API."""

from __future__ import annotations

import argparse
import http.client
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.parse import urlparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Chronos macOS Monitor bridge")
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:8765/api/monitor/observations",
    )
    parser.add_argument("--device-id")
    args = parser.parse_args()
    target = urlparse(args.endpoint)
    if target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("monitor endpoint must be local HTTP")

    root = Path(__file__).resolve().parents[3]
    command = [
        "swift",
        "run",
        "--package-path",
        "apps/mac-agent",
        "chronos-mac-agent",
    ]
    if args.device_id:
        command += ["--device-id", args.device_id]
    if os.environ.get("DEVELOPER_DIR"):
        command[0:1] = ["xcrun", "swift"]

    child = subprocess.Popen(
        command,
        cwd=root,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def stop(_signal=None, _frame=None) -> None:
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    connection: http.client.HTTPConnection | None = None
    path = target.path or "/api/monitor/observations"
    try:
        assert child.stdout is not None
        for line in child.stdout:
            if not line.strip():
                continue
            try:
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
                    print(f"Chronos Monitor ingest HTTP {response.status}", file=sys.stderr)
            except (OSError, http.client.HTTPException) as error:
                print(f"Chronos Monitor bridge reconnecting: {error}", file=sys.stderr)
                if connection is not None:
                    connection.close()
                    connection = None
    finally:
        if connection is not None:
            connection.close()
        stop()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
    return child.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
