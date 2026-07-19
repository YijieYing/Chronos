#!/bin/zsh

set -euo pipefail

ROOT="${0:A:h:h}"
cd "$ROOT"

swift run --package-path apps/mac-agent chronos-mac-agent "$@" \
  | PYTHONPATH=src python3 -m chronos.api.cli recognize
