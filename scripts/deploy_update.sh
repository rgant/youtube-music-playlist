#!/usr/bin/env bash
# Update this Mac to the pushed code, then restart the LaunchAgents. Run it on the machine that runs
# the schedule.
#
# `just deploy-update` wraps this file, as `just install-agents` wraps its own script. The sequence
# lives here so `set -euo pipefail` guards it and shellcheck reads it. A `just` recipe body gets
# neither.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

git pull
uv sync
bash scripts/install_launchagents.sh

echo
echo "loaded agents:"
launchctl list | grep library-radio || echo "no library-radio agent is loaded"
