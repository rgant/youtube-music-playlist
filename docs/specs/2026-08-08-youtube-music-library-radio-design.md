# YouTube Music library radio design

Date: 2026-08-08

## Purpose

Play any song from the YouTube Music library on the Kitchen Sonos Play:1, in
random order, without end.

The listener presses play one time. The music continues until the listener stops
it.

## Success criteria

- The station draws from every song in the catalogue, not a 500-song subset.
- The station never ends.
- The Sonos app shows the song title and the artist name.
- One unplayable song does not stop the music.
- Playback does not need a valid YouTube credential.

## Established facts

Each fact below comes from a test against the real speaker and the real account
on 2026-08-08.

| Fact                                                        | Evidence                                                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Sonos limits one service playlist to 500 tracks.            | Sonos documents this limit for service partners. See <https://docs.sonos.com/docs/add-play-shuffle-all>.                       |
| The Play:1 queue accepts at least 5000 tracks.              | A test pushed 5000 tracks. The speaker reported 5000.                                                                          |
| The Sonos app performs badly above 500 queued tracks.       | Owner experience.                                                                                                              |
| A radio stream has no queue and no track limit.             | The speaker played a local HTTP MP3 stream through `x-rincon-mp3radio://`.                                                     |
| One MP3 response can carry many songs back to back.         | A test streamed 7 songs. The speaker held `PLAYING` across 6 song changes.                                                     |
| Sonos shows ICY metadata and updates it live.               | The speaker showed `artist='Nirvana'` and `title='Territorial Pissings'`, then changed both.                                   |
| `ytmusicapi` OAuth fails for every authenticated call.      | Every authenticated call returned HTTP 400. Anonymous calls succeeded. See <https://github.com/sigma67/ytmusicapi/issues/813>. |
| `yt-dlp` resolves library songs without any credential.     | 8 of 10 sample songs resolved. Median time was 1.07 seconds.                                                                   |
| Some stored video IDs are dead.                             | 2 of 10 sample songs returned "Video unavailable".                                                                             |
| The YouTube Data API reads the existing playlists cheaply.  | A full read of 16,054 song IDs costs about 354 units of the 10,000 daily quota.                                                |
| The YouTube Data API cannot read the YouTube Music library. | The `likes` playlist holds YouTube likes, not library songs.                                                                   |
| A 5-minute run stayed stable.                               | The speaker held `PLAYING`. No song was skipped. No `ffmpeg` process leaked.                                                   |

## Constraints

- The owner has no disk space for a local copy of the library.
- The service runs on an Intel Mac Mini. Development happens on an arm64 Mac.
- `ffmpeg` comes from Homebrew. The Intel prefix is `/usr/local`. The arm64
  prefix is `/opt/homebrew`.
- The speaker stays on Sonos S1. Do not migrate to S2. S2 removes `customsd.htm`
  and closes every self-hosted path.
- A skip control is not required.

## Architecture

The service divides into five units. Each unit has one purpose. Each unit is
testable alone.

### `catalogue`

Owns the song store.

The store is a SQLite database. One table holds one row for each song. A row
holds the video ID, the title, the artist, the failure count, the last success
time, and the last played time.

- Opens the database in WAL mode. Two processes write to the database.
- Creates the table with `CREATE TABLE IF NOT EXISTS`. Holds the schema version
  in the `user_version` pragma.
- Reads the existing `Everything N` playlists through the YouTube Data API to
  fill an empty table.
- Merges a fresh library read into the table. New songs arrive. A row that
  already exists keeps its counts.
- If a resolve reports the video gone, raises the failure count on that row.
- If the failure count passes the prune threshold, deletes the row. The
  threshold is a setting.
- If a resolve fails for any other reason, writes nothing. A 403 reaches a song
  that plays, so a count against it deletes working songs.
- Returns the songs outside the no-repeat window.
- Records the played time after a song starts.

Depends on: `sqlite3`, the YouTube Data API, `ytmusicapi`.

Use the standard library `sqlite3` module alone. Do not add SQLAlchemy. Do not
add alembic. One table needs no ORM and no migration framework.

SQLite answers a concurrency problem, not a size problem. The station process
writes failure counts during playback. The refresh command writes new songs at
any time. A JSON file needs atomic writes, a lock, and merge logic. SQLite
provides all three.

### `selector`

Chooses the next song.

- Takes the candidate songs. Returns one video ID.
- Picks with a uniform random distribution.
- Performs no input and no output.

`catalogue` applies the no-repeat window because the window state lives in the
database. The window size is a setting. The window survives a restart.

Depends on: nothing.

### `resolver`

Turns a video ID into playable audio.

- Returns the direct audio URL, the title, and the artist.
- If the song does not resolve, raises a typed error. The type says whether a
  later attempt can succeed.
- Applies no policy. The caller decides what a failure means, and how many
  attempts one song gets.

Depends on: `yt-dlp`.

### `station`

Serves the endless MP3 stream over HTTP.

- Answers one GET request with one response that never ends.
- Asks `selector` for a song. Asks `resolver` for the audio URL.
- Runs `ffmpeg` to transcode the audio to MP3 at a constant bit rate.
- Writes the audio bytes to the response.
- If the client sends `Icy-MetaData: 1`, writes an ICY metadata block.
- If the song changes, sends a new metadata block. Otherwise sends an empty
  block.
- If `resolver` raises an error, skips to the next song. It never asks twice for
  one song.

Depends on: `selector`, `resolver`, `ffmpeg`.

### `control`

Starts and stops playback on the speaker.

- Finds the speaker by name.
- Points the speaker at the station URL with radio transport.
- Reports the transport state.

`control` never restarts playback on its own. The station is a passive server,
and the speaker starts playback. A radio station serves bytes to whoever
connects. It holds no view on whether a speaker must play.

A `STOPPED` transport cannot say whether a person stopped the speaker or the
stream broke. Both look the same from the network. Any rule written against that
state either fights the owner or goes silent for good.

Depends on: `soco`.

## Data flow

1. `catalogue` returns the songs outside the no-repeat window.
2. `selector` returns one video ID.
3. `resolver` returns the audio URL and the metadata.
4. `station` runs `ffmpeg` and writes MP3 bytes with ICY metadata.
5. The speaker plays the bytes as a radio station.
6. Steps 2 to 5 repeat without end.

Playback never adds a row. Playback writes the played time and the failure
count. It fills an empty title or artist with the resolved values, and it keeps
a stored non-empty value.

It deletes one row alone: a song whose resolve reports the video gone,
`prune_threshold` times in a row. Such a song can never play, so it must not
stay a candidate. Every other failure leaves the row as it stands.

The first catalogue holds the songs in the `Everything N` playlists. The library
holds more songs than the playlists hold. The refresh command adds the
difference. The owner runs the refresh command as the first task after this
project ends.

The refresh command also excludes a song whose library title holds `radio edit`
or `censored` as a whole word. A whole-word match keeps `The Uncensored Mix`,
which a substring match deletes. It deletes an excluded song from the catalogue.
A skip alone does nothing, because `bootstrap` already inserted that video ID.
The `Everything N` playlists carry video IDs with no titles, so this rule can
only run against a library title. The refresh command logs each excluded song,
so the owner can find a pattern that matches too much.

## Error handling

| Condition                                     | Response                                                        |
| --------------------------------------------- | --------------------------------------------------------------- |
| A song does not resolve.                      | Skip the song. Increase its failure count. Continue the stream. |
| Sonos opens a probe connection and closes it. | Treat the close as normal. Do not log an error.                 |
| The client disconnects.                       | Stop `ffmpeg`. Wait for the process to exit. End the response.  |
| `ffmpeg` exits early.                         | Move to the next song. Continue the stream.                     |
| The table holds no songs.                     | Refuse to start. Report the cause.                              |
| The stream stops at the speaker.              | The owner presses play. See `control`.                          |
| The library refresh fails.                    | Keep the existing rows. Report the cause. Playback continues.   |

Authentication sits off the playback path. If the YouTube credential fails, the
music continues. Only new songs stop arriving.

## Test strategy

Follow the preference order: no double, then fake, then stub.

- `selector`: unit tests. Pure functions need no double. Test the uniform
  distribution.
- The ICY encoder: unit tests. Test the length byte, the padding, and the
  payload.
- `catalogue`: unit tests against a temporary database. Use a real database, not
  a double. Test the merge, the prune, and the no-repeat window.
- Concurrent writes: one test opens two connections. One connection records a
  failure. The other connection merges new songs. Both writes must survive.
- `station`: integration tests against a fake resolver that returns a local
  audio file. No network. No mock.
- `resolver`: one integration test against a known-good video ID. This test
  needs the network.
- `control`: no automated test. Test by hand against the speaker.

Coverage must stay at or above 90 percent, as
`[tool.coverage.report] fail_under` requires.

## Toolchain migration

Move from pipenv to uv. Target Python 3.14.

- Delete `Pipfile` and `Pipfile.lock`.
- Create `pyproject.toml` from the cat-watcher configuration. Use hatchling as
  the build backend.
- Set `requires-python = ">=3.14"`.
- Add runtime and development dependencies with `uv add`. Never edit dependency
  lists by hand.
- Port `[tool.ruff]`, `[tool.pylint]`, `[tool.mypy]`,
  `[tool.pytest.ini_options]`, `[tool.coverage]`, `[tool.deptry]`,
  `[tool.pyproject-fmt]`, and `[tool.basedpyright]`.
- Set `[tool.basedpyright] venvPath = "."` and `venv = ".venv"`. uv creates
  `.venv`, not `.pixi/envs/`.
- Remove the pixi tables. uv replaces them.
- Remove the cat-watcher rules for alembic, djlint, and the ML stack. This
  project has none of them.
- Delete `mypy.ini`. The settings move into `pyproject.toml`.
- Delete `.pylintrc`. The settings move into `pyproject.toml`.
- Move source files into `src/youtube_music_library_radio/`.
- Move `common/logger.py` into the package. Keep the colour formatter.

The migration changes two formatting rules. Ruff sets the line length to 140 and
the quote style to double. The current code uses 100 and single quotes. The
formatters make both changes without help.

### Task runner

Use `just`. Follow the recipe style in the example justfile.

- Group recipes with `[group('...')]`. Use the groups `format`, `lint`, `test`,
  `deps`, and `util`.
- Call Python tools through `uv run`.
- Provide `just format`, `just lint`, `just test`, and `just check`.
  `just check` runs format, then lint, then test.
- Provide `just sync` for `uv sync`.
- Make `default` run the pre-commit workflow.

### Formatter configuration

`dprint.jsonc` and `.markdownlint.jsonc` arrive from the other projects without
change.

- `dprint.jsonc` holds the yaml, json, markdown, ruff, and toml plugins.
- `dprint.jsonc` excludes `**/pyproject.toml` from the toml plugin.
  `pyproject-fmt` owns that file.
- Delete `.markdownlint.json`. `.markdownlint.jsonc` replaces it.
- Install `dprint` and `markdownlint-cli` outside uv. Neither tool comes from
  PyPI.

### Homebrew tools

A `Brewfile` records every tool that Homebrew provides. `brew bundle` installs
them. `brew bundle check` reports a missing tool.

| Tool               | Purpose                                       | Needed on the Mac Mini                  |
| ------------------ | --------------------------------------------- | --------------------------------------- |
| `ffmpeg`           | Transcodes audio to MP3.                      | Yes. The station cannot run without it. |
| `uv`               | Creates the environment and runs the package. | Yes.                                    |
| `just`             | Runs the project tasks.                       | No. Development only.                   |
| `dprint`           | Formats markdown, json, toml, and yaml.       | No. Development only.                   |
| `markdownlint-cli` | Lints markdown.                               | No. Development only.                   |
| `shellcheck`       | Lints the install script.                     | No. Development only.                   |
| `shfmt`            | Formats the install script.                   | No. Development only.                   |

`brew bundle` has no group concept. Mark the runtime tools with a comment in the
`Brewfile`. The Mac Mini needs `ffmpeg` and `uv` alone.

Do not add `actionlint`. This project has no GitHub Actions workflow.

### Rename

The current name describes playlists. The project now serves radio.

The owner renamed the folder and the Sublime project files on 2026-08-08. Two
tasks remain.

- Rename the GitHub repository to `youtube-music-library-radio`.
- Name the package `youtube_music_library_radio`.

GitHub redirects the old URL. The git remote needs no change.

## Deployment

- Run `brew bundle` on the Mac Mini. This installs `ffmpeg` and `uv`.
- Find the `ffmpeg` path at run time. Do not hardcode a Homebrew prefix. The
  Intel prefix is `/usr/local`. The arm64 prefix is `/opt/homebrew`.
- Install a launchd agent named `name.robgant.youtube_music_library_radio`.
  Follow the existing naming convention.
- Set `KeepAlive` so launchd restarts the service after a crash.
- Provide an install script under `scripts/`, as cat-watcher does.

## Retired work

`library_playlist.py` goes away. The `Everything N` playlists stop being
infrastructure.

The playlists become a one-time source of song IDs. After the first read, the
SQLite catalogue is the source of truth.

This removes the playlist size arithmetic, the multi-playlist logic, and the
Data API write quota problem. The write quota allows about 200 song additions
each day, which never suited a library of this size.

Nothing updates the playlists after this project ends. The owner accepts this.
The playlists go stale by design.

The owner plans to delete the playlists. Two facts decide when that is safe.
`bootstrap` reads the playlists over OAuth, which stays valid for months.
`refresh` reads the library over browser cookies, which expire. A delete retires
the durable path and leaves the fragile one.

Before you delete the playlists, confirm that `refresh` reads the library. Then
confirm that every catalogue row appears in that read. A song in the playlists
but not in the library is lost with them.

## Open risks

| Risk                                         | Effect                                   | Response                                                             |
| -------------------------------------------- | ---------------------------------------- | -------------------------------------------------------------------- |
| `yt-dlp` breaks against YouTube.             | The station cannot resolve songs.        | Update `yt-dlp`. This is the main maintenance cost.                  |
| YouTube throttles requests from one address. | Songs resolve slowly or fail.            | Measured. See "Terms of service and account risk" below.             |
| Browser cookies expire fast.                 | The library read returns no songs.       | `refresh` raises and names `library-radio auth`. Playback continues. |
| Age-restricted songs never resolve.          | A small part of the library never plays. | The station skips them. It never prunes for a transient fault.       |
| Sonos changes the radio transport.           | Playback stops.                          | No mitigation. S1 firmware changes rarely.                           |

## Terms of service and account risk

Google offers no API that streams this audio. The YouTube Data API v3 serves
metadata alone. YouTube Music has no public API. This project uses `yt-dlp`,
which is against YouTube's Terms of Service. That is true at any volume.

### What one measurement showed

On 2026-08-15 the development machine sent about 300 resolve requests in 40
minutes from one address. YouTube then answered every request with a bot check,
which starts "Sign in to confirm". The block cleared inside the hour. Nothing
reached the owner's account.

The enforcement was address-level and temporary. This project saw no other
enforcement.

### Why the service sends no cookies

The station sends no cookies to `yt-dlp`. Nothing then ties a resolve request to
the owner's account, except the home address. Ordinary browsing shares that
address, so it is weak evidence.

Cookies move the risk from the address to the account. The yt-dlp wiki warns
that an account can be banned, and it names a throwaway account as the answer.
Cookies also change which client `yt-dlp` picks, which is the one untested route
to a different URL. The owner rejected that trade.

`refresh` does use cookies. It reads the library and moves no audio, which is
the same action the web app performs.

### The station's own rate

The station resolves one song at a time. A song lasts about three minutes, so
the service sends about one request every three minutes. That rate is far below
the rate that produced the block.

### The licensed alternative

The Sonos YouTube Music service is Google's own integration. Playback through it
carries none of this risk. A design that fills the Sonos queue from the
catalogue reaches the library without `yt-dlp`. The 500-song cap in
`library_playlist.py` was a playlist import limit, not a queue limit.

## Out of scope

- A download of the library to disk. The owner has no space.
- A skip control. The owner does not want one.
- Music Assistant. The owner chose the custom service.
- A migration to Sonos S2. S2 closes the self-hosted path.
- Support for a second speaker.
