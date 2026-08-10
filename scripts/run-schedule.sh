#!/bin/zsh

set -euo pipefail

ROOT="${0:A:h:h}"
cd "$ROOT"

if [[ ! -f web/dist/index.html ]]; then
  npm --prefix web run build
fi

PYTHONPATH=src python3 -m chronos.api.schedule_server "$@"
