"""Show a desktop notification banner through `osascript`.

A scheduled run writes its fault into a log nobody reads. `queue` keeps working from the songs that
already carry a pairing, so a dead credential stays invisible for months. The banner is the one
sign the owner gets.

macOS asks for notification permission the first time `osascript` shows a banner. An unanswered
prompt swallows the banner, so fire one from a terminal after each install.
"""

import logging
import subprocess
import typing

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_logger = logging.getLogger(__name__)
# A blocked notification centre must not hold the agent open until the next scheduled run.
_TIMEOUT_SECONDS = 10.0


def _default_run(args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    """Run `args` with no shell, and return the finished process."""
    # `args` is a list this module builds. No part of it reaches a shell.
    return subprocess.run(args, check=False, capture_output=True, timeout=_TIMEOUT_SECONDS)  # noqa: S603


def _escape(value: str) -> str:
    """Escape `value` for an AppleScript string in double quotes.

    The backslash goes first. A later pass escapes the backslash this function adds for a quote,
    which gives two of them. AppleScript reads an unknown escape as nothing, and no banner appears.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _payload(title: str, body: str) -> str:
    """Return the one AppleScript statement that shows the banner."""
    return f'display notification "{_escape(body)}" with title "{_escape(title)}"'


def notify(title: str, body: str, *, run_fn: Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]] = _default_run) -> bool:
    """Show one notification banner. Return True after `osascript` accepts it.

    Raises nothing. The caller already failed, and a second fault must not change the code it
    returns. Every route to False writes a log line, so the reason survives.
    """
    command = ["osascript", "-e", _payload(title, body)]
    try:
        result = run_fn(command)
    except FileNotFoundError, subprocess.TimeoutExpired:
        _logger.exception("osascript did not run")
        return False

    if result.returncode != 0:
        reported = result.stderr.decode(errors="replace").strip()
        _logger.error("osascript returned %d. %s", result.returncode, reported or "it named no reason")
        return False
    return True
