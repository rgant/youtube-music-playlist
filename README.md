# YouTube Music Library Radio

Keep a catalogue of the owner's YouTube Music library, and command a Sonos
speaker from it.

## How it works

- The catalogue is a SQLite database of every song in the library.
- `bootstrap` seeds it from the owner's `Everything N` YouTube playlists.
- `refresh` merges the live library into it, and deletes every excluded song.
- The catalogue records `last_played` and `failure_count` for every song. A
  player reads those columns to pass over the songs it played most recently.

This repository holds the catalogue and the credentials. It plays no audio.

## Risk

`refresh` and `auth` read the library with the owner's browser cookies, through
`ytmusicapi`. They read the library and move no audio, which is the same action
the web app performs. `bootstrap` reads the YouTube Data API v3 over OAuth,
which Google supports.

Google offers no API that streams this audio. The YouTube Data API v3 serves
metadata alone, and YouTube Music has no public API. A design that moves audio
therefore needs a route Google does not support. The Sonos YouTube Music service
is Google's own integration, and playback through it carries no such risk.

## Requirements

The machine that runs these commands needs `uv` alone. The other Homebrew tools
in the `Brewfile` serve development: `just`, `dprint`, `markdownlint-cli`,
`shellcheck`, and `shfmt`.

## Setup

Do these steps in order.

### 1. Install the Homebrew tools

```sh
brew bundle
```

Run `brew bundle check` to verify that no tool is absent.

### 2. Create the Python environment

```sh
uv sync
```

### 3. Write the OAuth token file

`bootstrap` reads the YouTube Data API over OAuth. It needs two files in the
repository root.

- `client_secret.apps.googleusercontent.com.json`. Download it from the Google
  Cloud console. Create an OAuth client ID of the type "TVs and Limited Input
  devices", because `ytmusicapi` uses the device-code flow. Save the download
  under that exact name.
- `oauth.json`. The command below writes it.

```sh
uv run ytmusicapi oauth
```

The command asks for a client ID and a client secret. Read both values from
`client_secret.apps.googleusercontent.com.json`. Then follow the device-code
instructions in the browser.

### 4. Write the browser headers file

`ytmusicapi` cannot read the library over OAuth. Every authenticated OAuth call
returns HTTP 400 against this account. See
[sigma67/ytmusicapi#813](https://github.com/sigma67/ytmusicapi/issues/813).
Browser cookies are the only route left.

```sh
uv run library-radio auth
```

Do these steps before you run the command. It reads the clipboard.

1. Open `https://music.youtube.com` in Chrome. Sign in.
2. Press Cmd-Option-I to open DevTools. Choose the Network tab.
3. Type `browse` in the filter box.
4. Click "Library" in the app, so the page makes a request.
5. Find a POST to `https://music.youtube.com/youtubei/v1/browse`.
6. Right-click that request. Choose Copy, then "Copy as cURL".
7. Run `uv run library-radio auth`.

A page load does not work. `ytmusicapi` needs the `cookie` header and the
`x-goog-authuser` header, and only a signed-in API call carries both. If the
capture holds neither, the command names which one is absent, prints these
steps, and writes nothing.

The clipboard is the default because a terminal cannot carry this text. One line
of a Chrome capture runs past 2000 characters. A terminal accepts about 1024 on
one line. A paste stalls there, and Ctrl-D does nothing.

To read a file instead, use `--from-file PATH`. To read a pipe, use
`--from-file -`. `auth` accepts a raw header block as well, for a browser that
offers one.

The command writes the new headers to `browser.json.new`, reads 25 songs, and
reports the count. Then it moves that file onto `browser.json`.

CAUTION: the capture holds live Google account cookies. Anyone who reads them
can open your account without a password. Keep the capture on the clipboard, or
in a file you delete. Never put it into a chat, an issue, or a message.

That read is the point of the command. It proves the new cookies work. If the
read returns no songs, the command names the fault and returns a non-zero value.
It then deletes `browser.json.new` and leaves the existing `browser.json` in
place. A failed `auth` never destroys a credential that still works.

`--headers` names another path. `refresh` takes the same option with the same
default, so change both together.

### 5. Fill the catalogue

`bootstrap` reads the `Everything N` YouTube playlists for their song IDs. It
reads `oauth.json` and `client_secret.apps.googleusercontent.com.json` from
step 3.

```sh
uv run library-radio bootstrap
uv run library-radio refresh
```

`bootstrap` seeds an empty catalogue one time. `refresh` adds the library songs
that the playlists never held. Run `refresh` again at any time to add new songs.
`refresh` reads the whole library, which takes a few minutes.

## Commands

| Command                   | Purpose                                       |
| ------------------------- | --------------------------------------------- |
| `library-radio bootstrap` | Fill an empty catalogue from the playlists.   |
| `library-radio auth`      | Write `browser.json` and verify it.           |
| `library-radio refresh`   | Merge the live library into the catalogue.    |
| `library-radio stop`      | Stop the speaker.                             |
| `library-radio status`    | Report the row count and the transport state. |

Each command returns 0 after success. Each command returns a non-zero value
after a failure.

`status` reads the row count from the catalogue, which is always available. It
then reads the transport state, which needs the speaker on the LAN. If discovery
fails, `status` still reports the row count and returns a non-zero value.

## Expired browser cookies

Browser cookies expire. `browser.json` stops working some months after you write
it.

YouTube answers an expired session with an empty library and no error. The
library read returns zero songs and raises nothing. A silent zero reads as "the
library holds no new songs". It means "the credential is dead".

`refresh` and `auth` both refuse an empty read. Each one raises, names
`browser.json`, and returns a non-zero value. The message says that the read
succeeded and returned nothing, which is a different fault from an absent file.

`library-radio auth` is the fix:

```sh
uv run library-radio auth
```

The command writes new cookies and verifies them against the library in one
step. If the verification fails, it leaves the existing `browser.json` in place.
You are never worse off than before you ran it.

A library that truly holds no songs also raises. This account holds about 18,000
songs, so that case cannot happen here.

## Excluded songs

`refresh` excludes a song whose library title holds `radio edit` or `censored`
as a whole word. It deletes an excluded song from the catalogue.

A deletion is necessary, not a tidy-up. `bootstrap` reads the `Everything N`
playlists, which carry video IDs and no titles, so it seeds every song before
any title exists. A skip alone leaves the row in place, and a player plays it.

The match reads whole words, so `The Uncensored Mix` stays. A plain substring
match deletes it.

`refresh` logs one line for each excluded song. The line names the pattern, the
video ID, the artist, and the title. Read that list after a run. It is the one
place an over-matching pattern shows itself, and a title such as
`Censored by the BBC` matches the rule for the wrong reason.

To change the rule, edit `_EXCLUDED_PATTERNS` in
`src/youtube_music_library_radio/refresh.py`.

A deletion is final. The catalogue holds no history.

To recover from a wrong pattern, first correct the pattern. Then run `bootstrap`
and `refresh` again. `bootstrap` restores a song that the `Everything N`
playlists hold. `refresh` restores a song that the library holds. A restored row
starts with no `failure_count` and no `last_played`. A row that survived keeps
both values.

Copy the database before the first run with a new pattern. See
[The catalogue on another machine](#the-catalogue-on-another-machine).

## Settings

Every setting reads an environment variable with the `YTM_RADIO_` prefix.

| Variable                     | Default                                                        | Purpose                                         |
| ---------------------------- | -------------------------------------------------------------- | ----------------------------------------------- |
| `YTM_RADIO_DATABASE_PATH`    | `~/.local/share/youtube-music-library-radio/catalogue.sqlite3` | The catalogue file.                             |
| `YTM_RADIO_SPEAKER_NAME`     | `Kitchen`                                                      | The name of the Sonos speaker.                  |
| `YTM_RADIO_NO_REPEAT_WINDOW` | `2000`                                                         | How many recently played songs stay off-limits. |
| `YTM_RADIO_PRUNE_THRESHOLD`  | `3`                                                            | How many permanent failures delete a song.      |

`YTM_RADIO_NO_REPEAT_WINDOW` sets the window `catalogue.candidate_songs`
excludes. A value of zero excludes nothing. A value at least as large as the row
count also excludes nothing.

## The Everything N playlists

The account holds the playlists `Everything 1` through `Everything 33`. They are
the residue of an earlier design. `bootstrap` reads them one time, for the song
IDs they hold.

Nothing writes to those playlists now. They go stale by design. The catalogue is
the source of truth.

## The catalogue on another machine

The catalogue holds derived data. To set up a second machine, run the setup
steps there. `bootstrap` rebuilds every row from the playlists, and `refresh`
adds the library songs. Copy the credential files across, because git ignores
them:

- `client_secret.apps.googleusercontent.com.json`
- `oauth.json`
- `browser.json`

The machine that plays songs owns the catalogue. It writes `last_played` and
`failure_count` for every song it plays. A copy on a second machine goes stale
at once. Move a catalogue in one direction only, at setup time.

To move a catalogue that holds playback history, stop the player first. Then
write a single-file snapshot and copy that file:

```sh
sqlite3 ~/.local/share/youtube-music-library-radio/catalogue.sqlite3 \
  "VACUUM INTO '/tmp/catalogue-snapshot.sqlite3'"
```

Never copy `catalogue.sqlite3` with `cp`, `scp`, or `rsync` while a player runs.
WAL journal mode holds recent writes in a separate `-wal` file. A copy of the
main file alone loses those writes, and it still reports a plausible row count.
`VACUUM INTO` writes one consistent file with no `-wal` companion.

## Development

```sh
just check
```

`just check` runs every linter and the test suite. Run `just --list` for the
other recipes.

## License

GPL-3.0-or-later. See `LICENSE`.
