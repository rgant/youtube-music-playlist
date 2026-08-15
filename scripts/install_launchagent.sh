#!/usr/bin/env bash
# Install the youtube-music-library-radio LaunchAgent on this Mac.
#
# Usage:
#   install_launchagent.sh           render the plist, stop the agent, then start it
#   install_launchagent.sh --stop    bootout the agent and leave it down
#   install_launchagent.sh --start   render the plist and bootstrap the agent
#
# The split lets a deploy update the catalogue with the agent stopped.
#
# `launchctl bootstrap` fails with `Bootstrap failed: 5: Input/output error` when the previous
# instance of the agent has not finished unwinding, and launchd reports the label gone long before
# that is true. start_agent retries that race. It prints what launchctl said on the last attempt,
# because "already loaded" and the race read the same way from the exit code alone.

set -euo pipefail

LABEL="name.robgant.youtube_music_library_radio"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${SCRIPT_DIR}/${LABEL}.plist.template"
LAUNCHAGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST="${LAUNCHAGENTS_DIR}/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/youtube-music-library-radio"
# The same path the plist template names in ProgramArguments. Both must change together.
PROGRAM="${REPO_DIR}/.venv/bin/library-radio"
UID_NUM="$(id -u)"
TARGET="gui/${UID_NUM}/${LABEL}"
: "${BOOTSTRAP_ATTEMPTS:=5}"
: "${BOOTSTRAP_RETRY_SECONDS:=3}"

RENDER_TMP=""

cleanup() {
	if [[ -n "${RENDER_TMP}" ]]; then
		rm -f "${RENDER_TMP}"
	fi
}
trap cleanup EXIT

# Prepare one value for substitution into the plist. Two escapes run, in this order.
#
#   1. XML: a plist is XML, so `&`, `<`, and `>` in a path must become entities.
#   2. sed: `sed` reads `\`, `&`, and the `|` delimiter as special inside a replacement. A `&` in
#      $HOME or in the repository path otherwise becomes the matched placeholder, and the rendered
#      path is wrong in a way that `plutil -lint` still accepts.
plist_value() {
	printf '%s' "$1" \
		| sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
		| sed -e 's/[\\&|]/\\&/g'
}

require_program() {
	if [[ ! -x "${PROGRAM}" ]]; then
		echo "${PROGRAM} is absent or not executable." >&2
		echo "Run \`uv sync\` in ${REPO_DIR}, then run this script again." >&2
		exit 69
	fi
}

render_plist() {
	local homebrew_bin
	homebrew_bin="$(brew --prefix)/bin"
	mkdir -p "${LAUNCHAGENTS_DIR}" "${LOG_DIR}"

	# Render to a temporary file. A redirect straight onto ${PLIST} truncates the installed plist
	# before `sed` reads the template, so an absent template leaves an empty plist behind.
	RENDER_TMP="$(mktemp "${TMPDIR:-/tmp}/${LABEL}.XXXXXX")"
	sed \
		-e "s|__REPO_DIR__|$(plist_value "${REPO_DIR}")|g" \
		-e "s|__HOMEBREW_BIN__|$(plist_value "${homebrew_bin}")|g" \
		-e "s|__LOG_DIR__|$(plist_value "${LOG_DIR}")|g" \
		"${TEMPLATE}" >"${RENDER_TMP}"

	if grep -q '__[A-Z_]*__' "${RENDER_TMP}"; then
		echo "the rendered plist still holds a placeholder:" >&2
		grep -o '__[A-Z_]*__' "${RENDER_TMP}" | sort -u >&2
		echo "Add the missing substitution to this script, or fix ${TEMPLATE}." >&2
		return 1
	fi

	mv "${RENDER_TMP}" "${PLIST}"
	RENDER_TMP=""
	echo "wrote ${PLIST}"
}

stop_agent() {
	# A bootout failure is expected when the agent is not loaded. Swallow it.
	launchctl bootout "${TARGET}" 2>/dev/null || true
	echo "stopped ${LABEL}"
}

start_agent() {
	local attempt output=""
	for ((attempt = 1; attempt <= BOOTSTRAP_ATTEMPTS; attempt++)); do
		if output="$(launchctl bootstrap "gui/${UID_NUM}" "${PLIST}" 2>&1)"; then
			echo "loaded ${LABEL}"
			return 0
		fi
		if ((attempt < BOOTSTRAP_ATTEMPTS)); then
			echo "  bootstrap attempt ${attempt}/${BOOTSTRAP_ATTEMPTS} failed, retrying in ${BOOTSTRAP_RETRY_SECONDS}s"
			sleep "${BOOTSTRAP_RETRY_SECONDS}"
		fi
	done

	echo "launchctl did not load ${LABEL}" >&2
	echo "launchctl said: ${output}" >&2
	echo "Read that message first. \"already loaded\" means the agent runs now." >&2
	echo "If the agent is down, recover it with a bare bootstrap, which cannot race:" >&2
	echo "  launchctl bootstrap gui/${UID_NUM} ${PLIST}" >&2
	return 1
}

case "${1-}" in
--stop)
	stop_agent
	;;
--start)
	require_program
	render_plist
	start_agent
	;;
"")
	require_program
	render_plist
	stop_agent
	start_agent
	;;
*)
	echo "usage: $(basename "$0") [--stop | --start]" >&2
	exit 64
	;;
esac
