# YouTube Music Library Radio

Stream the owner's YouTube Music library to a Sonos speaker as an endless radio
station.

Sonos limits a playlist to 500 tracks. A radio stream holds no queue, so it
holds no limit. This project serves one MP3 stream that never ends. The speaker
plays that stream as a radio station.

## How it works

- The catalogue is a SQLite database of every song in the library.
- The station answers one GET request with one response that never ends.
- The resolver turns a video ID into a playable audio URL with `yt-dlp`.
- `ffmpeg` transcodes each song to MP3.
- The station writes ICY metadata, so the Sonos app shows the title and the
  artist.
- When a song ends, the next song starts in the same response.

The station is a passive server. It serves bytes to whoever connects. It never
starts playback on the speaker, and it never restarts playback.

## Risk

Google offers no API that streams this audio. The YouTube Data API v3 serves
metadata alone. YouTube Music has no public API. This project uses `yt-dlp`,
which is against YouTube's Terms of Service. That is true at any volume.

The station sends no cookies to `yt-dlp`. Nothing then ties a resolve request to
the owner's account, except the home address. Ordinary browsing shares that
address, so it is weak evidence.

One measurement, on 2026-08-15: about 300 requests in 40 minutes came from one
address. YouTube then answered every request with a bot check, which starts
"Sign in to confirm". The block cleared inside the hour. Nothing reached the
account.

The station resolves one song at a time, and a song lasts about three minutes.
It therefore sends about one request every three minutes, far below the rate
that caused that block.

Cookies move the risk from the address to the account. The yt-dlp wiki warns
that an account can be banned, and it names a throwaway account as the answer.
This project therefore sends no cookies to `yt-dlp`.

`refresh` and `auth` do use cookies. They read the library and move no audio,
which is the same action the web app performs.

The Sonos YouTube Music service is Google's own integration, and playback
through it carries none of this. Read
`docs/specs/2026-08-08-youtube-music-library-radio-design.md` for the
comparison.

## Requirements

The server that runs the service needs `ffmpeg` and `uv` alone. The other
Homebrew tools in the `Brewfile` serve development: `just`, `dprint`,
`markdownlint-cli`, `shellcheck`, and `shfmt`.

`ffmpeg` must be on `PATH`. The service reads `PATH` at run time, because the
Intel Homebrew prefix and the arm64 Homebrew prefix differ.

## Setup

Do these steps in order.

### 1. Install the Homebrew tools

```sh
brew bundle
```

Run `brew bundle check` to confirm that no tool is absent.

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

### 6. Install the launchd agent

```sh
bash scripts/install_launchagent.sh
```

The script renders the plist template in `scripts/` into
`~/Library/LaunchAgents`. It fills in the repository path, the Homebrew `bin`
directory, and the log directory. The agent runs `library-radio serve`.
`KeepAlive` restarts the service after a crash. The logs go to
`~/Library/Logs/youtube-music-library-radio/`.

The speaker does not reconnect by itself. If the service restarts, or if the
speaker reboots, run `library-radio play` again. The speaker reports `STOPPED`
until you do.

To stop the agent, run the script with `--stop`. To start it again, run the
script with `--start`.

### 7. Play the station

```sh
uv run library-radio play
```

## Commands

| Command                   | Purpose                                        |
| ------------------------- | ---------------------------------------------- |
| `library-radio serve`     | Serve the endless stream. This command blocks. |
| `library-radio bootstrap` | Fill an empty catalogue from the playlists.    |
| `library-radio auth`      | Write `browser.json` and verify it.            |
| `library-radio refresh`   | Merge the live library into the catalogue.     |
| `library-radio play`      | Point the speaker at the station.              |
| `library-radio stop`      | Stop the speaker.                              |
| `library-radio status`    | Report the row count and the transport state.  |

Each command returns 0 after success. Each command returns a non-zero value
after a failure.

`serve` starts no thread that watches the speaker. A `STOPPED` transport cannot
say whether a person stopped the speaker or the stream broke. Both look the same
from the network. `play` exists for a person to call on purpose.

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

## Skipped songs

YouTube answers a valid request with HTTP 403 at a rate that no request option
changes. The same song plays on the next attempt. See
[yt-dlp#17395](https://github.com/yt-dlp/yt-dlp/issues/17395), which records the
behaviour with no root cause and no fix.

The station asks for each song one time. If the resolve fails, or if the song
sends no audio, the station moves to the next song and keeps the row. A later
pick reaches that song again. A repeat attempt costs silence and buys nothing,
because the next song plays as often as the same song does.

After five silent songs in a row, the station waits five seconds before the next
one. After twenty, it ends the response. A run that long points at the network,
not at one song.

A 403 never raises the failure count of a song. Only a resolve that reports the
video gone does that, and `YTM_RADIO_PRUNE_THRESHOLD` counts those. A 403
reaches a working song, so a count against it deletes songs that play.

The log names each skipped song. A long run of skips points at the network, or
at a block on this IP address. It does not point at the catalogue.

## Excluded songs

`refresh` excludes a song whose library title holds `radio edit` or `censored`
as a whole word. It deletes an excluded song from the catalogue.

A deletion is necessary, not a tidy-up. `bootstrap` reads the `Everything N`
playlists, which carry video IDs and no titles, so it seeds every song before
any title exists. A skip alone leaves the row in place, and the station plays
it.

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

| Variable                      | Default                                                        | Purpose                                       |
| ----------------------------- | -------------------------------------------------------------- | --------------------------------------------- |
| `YTM_RADIO_DATABASE_PATH`     | `~/.local/share/youtube-music-library-radio/catalogue.sqlite3` | The catalogue file.                           |
| `YTM_RADIO_SPEAKER_NAME`      | `Kitchen`                                                      | The name of the Sonos speaker.                |
| `YTM_RADIO_STATION_HOST`      | empty                                                          | The address the station binds and advertises. |
| `YTM_RADIO_STATION_PORT`      | `8900`                                                         | The port the station binds.                   |
| `YTM_RADIO_BITRATE_KBPS`      | `128`                                                          | The MP3 bitrate.                              |
| `YTM_RADIO_NO_REPEAT_WINDOW`  | `2000`                                                         | How many recent songs the station excludes.   |
| `YTM_RADIO_PRUNE_THRESHOLD`   | `3`                                                            | How many permanent failures delete a song.    |
| `YTM_RADIO_METADATA_INTERVAL` | `16000`                                                        | The ICY metadata interval, in bytes.          |
| `YTM_RADIO_STATION_NAME`      | `My Library Radio`                                             | The station name the speaker displays.        |

`YTM_RADIO_STATION_HOST` sets two things at once: the address the station binds,
and the address the station gives the speaker to fetch from. An empty value
binds every interface, and `play` reads the LAN address of this machine.

A fixed value needs a DHCP reservation for this machine in the router. Without a
reservation, DHCP can give this machine a different address. `serve` then fails
to bind with an `OSError`, and `KeepAlive` restarts that failure every ten
seconds.

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

The machine that runs the service owns the catalogue. It writes `last_played`
and `failure_count` for every song it plays. A copy on a second machine goes
stale at once. Move a catalogue in one direction only, at setup time.

To move a catalogue that holds playback history, stop the service first. Then
write a single-file snapshot and copy that file:

```sh
sqlite3 ~/.local/share/youtube-music-library-radio/catalogue.sqlite3 \
  "VACUUM INTO '/tmp/catalogue-snapshot.sqlite3'"
```

Never copy `catalogue.sqlite3` with `cp`, `scp`, or `rsync` while the service
runs. WAL journal mode holds recent writes in a separate `-wal` file. A copy of
the main file alone loses those writes, and it still reports a plausible row
count. `VACUUM INTO` writes one consistent file with no `-wal` companion.

## Development

```sh
just check
```

`just check` runs every linter and the test suite. Run `just --list` for the
other recipes.

## License

GPL-3.0-or-later. See `LICENSE`.
