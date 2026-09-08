"""Tests for youtube_music_library_radio.notify."""

import subprocess
import typing

from youtube_music_library_radio.notify import notify

if typing.TYPE_CHECKING:
    from collections.abc import Sequence


class _FakeRunner:
    """Record the command and answer with a chosen result, so no banner reaches the screen."""

    def __init__(self, *, returncode: int = 0, stderr: bytes = b"", raises: Exception | None = None) -> None:
        self.commands: list[list[str]] = []
        self._returncode: int = returncode
        self._stderr: bytes = stderr
        self._raises: Exception | None = raises

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        """Record `args`, then raise or answer."""
        self.commands.append(list(args))
        if self._raises is not None:
            raise self._raises
        return subprocess.CompletedProcess(args=list(args), returncode=self._returncode, stderr=self._stderr)


def test_notify_runs_osascript_with_the_title_and_the_body() -> None:
    """The banner is the one sign of a failed scheduled run, so the title and the body must reach `osascript`."""
    runner = _FakeRunner()

    assert notify("refresh failed", "the credential expired", run_fn=runner) is True

    command = runner.commands[0]
    assert command[0] == "osascript"
    assert "display notification" in command[2]
    assert "refresh failed" in command[2]
    assert "the credential expired" in command[2]


def test_notify_escapes_a_quote_in_the_body() -> None:
    """An error message holds a quoted file name. An unescaped quote ends the AppleScript string early."""
    runner = _FakeRunner()

    _ = notify("failed", 'no such file: "browser.json"', run_fn=runner)

    assert '\\"browser.json\\"' in runner.commands[0][2]


def test_notify_escapes_a_backslash_before_it_escapes_a_quote() -> None:
    """A second pass over an added backslash escapes it twice. AppleScript then shows no banner."""
    runner = _FakeRunner()

    _ = notify("failed", 'a\\b"c', run_fn=runner)

    assert 'a\\\\b\\"c' in runner.commands[0][2]


def test_notify_reports_false_when_osascript_is_absent() -> None:
    """A run on a machine that is not a Mac must not change the exit code of the command that failed."""
    runner = _FakeRunner(raises=FileNotFoundError(2, "No such file or directory", "osascript"))

    assert notify("failed", "body", run_fn=runner) is False


def test_notify_reports_false_when_osascript_hangs() -> None:
    """A blocked notification centre must not hold the agent open, and it must raise nothing."""
    runner = _FakeRunner(raises=subprocess.TimeoutExpired(cmd=["osascript"], timeout=10))

    assert notify("failed", "body", run_fn=runner) is False


def test_notify_reports_false_after_a_non_zero_exit() -> None:
    """The system refuses a banner until the owner grants permission, and it reports that refusal here."""
    runner = _FakeRunner(returncode=1, stderr=b"not authorized")

    assert notify("failed", "body", run_fn=runner) is False
