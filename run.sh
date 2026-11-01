#!/bin/bash
# automatic-windhawk-testing — build + test + report in one command.
set -u
REPO="$(cd "$(dirname "$0")" && pwd)"
[ -x "$REPO/.sandbox/build/awt-host.exe" ] || true
cd "$REPO" || exit 1
python3 -m awt.cli run "$@"
