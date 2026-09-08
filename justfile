# youtube-music-library-radio task runner
# Run `just --list` to see all available recipes

# Recipe arguments reach the shell as $1, $2, and so on. Without this, just joins them into one
# string and quoting is lost, so `just test -m "not network"` splits and pytest reads a path.
set positional-arguments := true

# Default recipe runs the full pre-commit gate
default: format check

# CI gate: lint, then test
[group('check')]
check: lint test

# Format all files
[group('format')]
format:
    uv run pyproject-fmt --no-print-diff pyproject.toml
    dprint fmt
    shfmt --write .
    markdownlint --fix .
    uv run ruff check --fix-only src tests
    uv run ruff format src tests

# Run all linters
[group('lint')]
lint:
    uv run ruff check src tests
    uv run basedpyright src tests
    uv run mypy
    uv run pylint src tests
    uv run deptry .
    find . \( -name .venv -o -name node_modules \) -prune -o -name '*.sh' -type f -exec shellcheck {} +
    markdownlint .

# Run pytest
[group('test')]
test *args:
    uv run pytest "$@"

# Run pytest with coverage report
[group('test')]
test-cov *args:
    uv run pytest --cov "$@"

# Sync project dependencies
[group('deps')]
sync:
    uv sync

# Check for outdated dependencies
[group('deps')]
outdated:
    uv tree --outdated --depth 1

# Install Homebrew dependencies from the Brewfile
[group('deps')]
brew:
    brew bundle

# Check that every Brewfile dependency is installed
[group('deps')]
brew-check:
    brew bundle check

# Delete Python/pytest/mypy/ruff/basedpyright cache directories
[confirm]
[group('util')]
clean:
    find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache -o -name .basedpyright \) -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name '*.pyc' -delete 2>/dev/null || true

# Update this Mac to the pushed code, then restart the agents. Run it on the mini
[group('deploy')]
deploy-update:
    git pull
    uv sync
    just install-agents
    just agents-status

# Install or refresh the launchd agents on this Mac
[group('deploy')]
install-agents:
    bash scripts/install_launchagents.sh

# Remove the launchd agents from this Mac
[group('deploy')]
uninstall-agents:
    bash scripts/uninstall_launchagents.sh

# Report which launchd agents are loaded
[group('deploy')]
agents-status:
    launchctl list | grep library-radio || echo "no library-radio agent is loaded"

# Run the refresh agent now, and do not wait for its hour
[group('deploy')]
agents-kick:
    launchctl kickstart -k "gui/$(id -u)/com.robgant.library-radio.refresh"

# Show one test banner, so macOS asks for notification permission
[group('deploy')]
agents-notify-test:
    osascript -e 'display notification "A failed refresh looks like this." with title "library-radio refresh failed"'
