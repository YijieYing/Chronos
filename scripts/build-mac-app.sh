#!/bin/zsh

set -euo pipefail

ROOT="${0:A:h:h}"
WEB_RESOURCES="$ROOT/apps/mac-app/Sources/ChronosMacApp/Resources/Web"
SWIFT=(swift)

if [[ -d /Applications/Xcode.app/Contents/Developer ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  SWIFT=(xcrun swift)
fi

export CLANG_MODULE_CACHE_PATH="${TMPDIR:-/tmp}/chronos-clang-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="${TMPDIR:-/tmp}/chronos-swiftpm-cache"

cd "$ROOT"
npm --prefix web run build
mkdir -p "$WEB_RESOURCES"
rsync -a --delete --exclude='.gitkeep' web/dist/ "$WEB_RESOURCES/"
"${SWIFT[@]}" build --package-path apps/mac-app

echo "Chronos macOS shell built with bundled web resources."
