#!/usr/bin/env bash
# Install, or refresh, the library-radio LaunchAgents on this Mac.
#
# Usage:
#   install_launchagents.sh           render the plists, stop every agent, then start every agent
#   install_launchagents.sh --stop    bootout every agent and leave it down
#   install_launchagents.sh --start   render the plists and bootstrap every agent
#
# The split lets a deploy move the catalogue with every agent down, and not underneath a run.
#
# `launchctl bootstrap` fails with `Bootstrap failed: 5: Input/output error` when the previous
# instance of an agent has not finished unwinding. launchd reports the label gone before that is
# true. Two behaviors answer that race:
#
#   1. Every agent goes down before any comes up, so each one gets the longest head start.
#   2. A failed bootstrap is retried, then reported. An abort leaves a later agent down in silence.

set -euo pipefail

# Each name here needs an entry in `_AGENT_NAMES` in render_plists.py and a template file.
AGENTS=(refresh queue)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHAGENTS_DIR="${HOME}/Library/LaunchAgents"
UID_NUM="$(id -u)"
: "${BOOTSTRAP_ATTEMPTS:=5}"
: "${BOOTSTRAP_RETRY_SECONDS:=3}"

plist_for() { echo "${LAUNCHAGENTS_DIR}/com.robgant.library-radio.${1}.plist"; }
target_for() { echo "gui/${UID_NUM}/com.robgant.library-radio.${1}"; }

render_plists() {
	mkdir -p "${LAUNCHAGENTS_DIR}"
	# render_plists creates the log directory as a side effect, so no mkdir is needed for it.
	(
		cd "${REPO_DIR}" \
			&& uv run python -m youtube_music_library_radio.render_plists --output "${LAUNCHAGENTS_DIR}"
	)
}

stop_agents() {
	for agent in "${AGENTS[@]}"; do
		# A bootout of an agent that is already down reports an error. Swallow it.
		launchctl bootout "$(target_for "${agent}")" 2>/dev/null || true
		echo "stopped ${agent}"
	done
}

# Bootstrap one agent and retry the race above. Returns non-zero after every attempt failed.
start_agent() {
	local agent="$1" attempt
	for ((attempt = 1; attempt <= BOOTSTRAP_ATTEMPTS; attempt++)); do
		if launchctl bootstrap "gui/${UID_NUM}" "$(plist_for "${agent}")" 2>/dev/null; then
			echo "loaded ${agent}"
			return 0
		fi
		if ((attempt < BOOTSTRAP_ATTEMPTS)); then
			echo "  ${agent}: bootstrap attempt ${attempt}/${BOOTSTRAP_ATTEMPTS} failed. Retrying in ${BOOTSTRAP_RETRY_SECONDS}s"
			sleep "${BOOTSTRAP_RETRY_SECONDS}"
		fi
	done
	return 1
}

start_agents() {
	local failed=()
	for agent in "${AGENTS[@]}"; do
		start_agent "${agent}" || failed+=("${agent}")
	done

	if ((${#failed[@]} == 0)); then
		echo "all agents loaded: ${AGENTS[*]}"
		return 0
	fi

	echo "FAILED to load: ${failed[*]}" >&2
	echo "Recover each one with a bare bootstrap, which cannot race because it is already down:" >&2
	for agent in "${failed[@]}"; do
		echo "  launchctl bootstrap gui/${UID_NUM} $(plist_for "${agent}")" >&2
	done
	echo "Do not run this script again to recover. Its bootout takes down a working agent." >&2
	return 1
}

case "${1-}" in
--stop)
	stop_agents
	;;
--start)
	render_plists
	start_agents
	;;
"")
	render_plists
	stop_agents
	start_agents
	;;
*)
	echo "usage: $(basename "$0") [--stop | --start]" >&2
	exit 64
	;;
esac
