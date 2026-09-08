"""Write the launchd LaunchAgent files that run this project on a schedule.

Each template under `scripts/plists/` carries `__NAME__` placeholders. This module fills them from
`Settings` and writes the result into the directory `--output` names. On the machine that runs the
schedule that directory is `~/Library/LaunchAgents`.

The render reads `load_settings`, so the agent and a terminal run act on the same values. It also
rejects a bad refresh hour before launchd reads the file. A bash render repeats both.
"""

import argparse
import logging
import re
import typing
from pathlib import Path

from youtube_music_library_radio.logger import create_handler
from youtube_music_library_radio.settings import Settings, load_settings

if typing.TYPE_CHECKING:
    from collections.abc import Sequence

_logger = logging.getLogger(__name__)

# `install_launchagents.sh` bootstraps this same list. A new agent needs a name here, a branch in
# `_substitutions_for`, a template file, and the same name in that script.
_AGENT_NAMES: tuple[str, ...] = ("refresh",)
_LABEL_PREFIX = "com.robgant.library-radio"
# The logs sit beside the catalogue, so one directory holds every file the agent owns.
_LOG_DIRNAME = "logs"
# A crossed placeholder name renders nothing and raises nothing, so the output is read again. A
# plist that keeps `__REPO_DIR__` names a program that is absent, and launchd reports that alone.
_PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")


def _repo_dir() -> Path:
    """Return the repository root, read from this file's path so any working directory works."""
    return Path(__file__).resolve().parents[2]


def _log_dir(settings: Settings) -> Path:
    """Return the directory that holds the launchd output of every agent."""
    return settings.database_path.parent / _LOG_DIRNAME


def _substitutions_for(agent: str, *, settings: Settings, repo_dir: Path) -> dict[str, str]:
    """Return the placeholder table for one agent.

    launchd gives an agent no shell, so every setting reaches it through the plist. An absent
    variable gives the agent a default and raises nothing, which sends it to the wrong speaker and
    the wrong catalogue.

    Raises `ValueError` for an agent name this function has no table for.
    """
    if agent != "refresh":
        message = f"unknown agent {agent!r}"
        raise ValueError(message)

    return {
        "__REPO_DIR__": str(repo_dir),
        "__LOG_DIR__": str(_log_dir(settings)),
        "__HOUR__": str(settings.refresh_hour),
        "__MINUTE__": str(settings.refresh_minute),
        "__DATABASE_PATH__": str(settings.database_path),
        "__SPEAKER_NAME__": settings.speaker_name,
        "__TRUST_RATIO__": str(settings.trust_ratio),
        "__MISSING_THRESHOLD__": str(settings.missing_threshold),
        "__QUEUE_SIZE__": str(settings.queue_size),
        "__QUEUE_WINDOW_DAYS__": str(settings.queue_window_days),
        "__HARVEST_TOLERANCE__": str(settings.harvest_tolerance),
    }


def _render_one(agent: str, *, template_dir: Path, output_dir: Path, subs: dict[str, str]) -> Path:
    """Fill one template and write it. Raises `RuntimeError` for a placeholder no substitution names."""
    source = template_dir / f"{_LABEL_PREFIX}.{agent}.plist.template"
    target = output_dir / f"{_LABEL_PREFIX}.{agent}.plist"
    content = source.read_text(encoding="utf-8")
    for key, value in subs.items():
        content = content.replace(key, value)

    leftover = _PLACEHOLDER_RE.search(content)
    if leftover is not None:
        message = f"unrendered placeholder {leftover.group(0)} in {agent}"
        raise RuntimeError(message)

    _ = target.write_text(content, encoding="utf-8")
    return target


def render(*, output_dir: Path, settings: Settings, template_dir: Path | None = None) -> tuple[Path, ...]:
    """Write one plist for each agent into `output_dir`, and return the paths it wrote."""
    repo_dir = _repo_dir()
    templates = repo_dir / "scripts" / "plists" if template_dir is None else template_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    # launchd starts no agent whose `StandardOutPath` names a directory that is absent.
    _log_dir(settings).mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for agent in _AGENT_NAMES:
        subs = _substitutions_for(agent, settings=settings, repo_dir=repo_dir)
        written.append(_render_one(agent, template_dir=templates, output_dir=output_dir, subs=subs))
    return tuple(written)


def main(argv: Sequence[str] | None = None) -> int:
    """Render every agent plist into the directory `--output` names, and return 0."""
    logging.basicConfig(level=logging.INFO, handlers=[create_handler()])
    parser = argparse.ArgumentParser(
        prog="python -m youtube_music_library_radio.render_plists",
        description="Write the LaunchAgent plist files from the current settings.",
    )
    _ = parser.add_argument("--output", type=Path, required=True, help="the directory to write each plist into")
    args = parser.parse_args(argv)

    for path in render(output_dir=typing.cast("Path", args.output), settings=load_settings()):
        _logger.info("rendered %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
