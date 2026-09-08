#!/usr/bin/env bash
# Remove the library-radio LaunchAgents from this Mac. The templates under scripts/plists/ stay.

set -euo pipefail

# Matches `AGENTS` in install_launchagents.sh.
AGENTS=(refresh)
LAUNCHAGENTS_DIR="${HOME}/Library/LaunchAgents"
UID_NUM="$(id -u)"

for agent in "${AGENTS[@]}"; do
	target="gui/${UID_NUM}/com.robgant.library-radio.${agent}"
	plist="${LAUNCHAGENTS_DIR}/com.robgant.library-radio.${agent}.plist"

	launchctl bootout "${target}" 2>/dev/null || true
	rm -f "${plist}"
	echo "removed ${agent}"
done
