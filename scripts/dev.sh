#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -q -r requirements.txt
if [ ! -d web/node_modules ]; then
  npm --prefix web ci
fi
npm --prefix web run build
export CASCADE_PORT="${CASCADE_PORT:-47123}"
exec python3 -m cascade_panel
