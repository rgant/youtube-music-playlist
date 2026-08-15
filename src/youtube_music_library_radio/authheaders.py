"""Turn what a browser gives you into the header block `ytmusicapi` wants.

`ytmusicapi` asks you to paste request headers and prints no instructions. Chrome offers "Copy as
cURL" and no header copy, so a person following the prompt has nothing that works. This module
accepts either form and reports what is absent.

`ytmusicapi` needs two headers: `cookie` and `x-goog-authuser`. Only a signed-in call to the
InnerTube API carries both. A page load carries no cookie, and `generate_204` carries no
`x-goog-authuser`.
"""

import shlex

# The flags a Chrome cURL copy uses for a header and for the cookies.
_HEADER_FLAGS = ("-H", "--header")
_COOKIE_FLAGS = ("-b", "--cookie")

# The two headers `ytmusicapi.auth.browser.setup_browser` refuses to work without.
_REQUIRED = ("cookie", "x-goog-authuser")

CURL_INSTRUCTIONS = """\
Capture a signed-in request from YouTube Music:

  1. Open https://music.youtube.com in Chrome. Make sure you are signed in.
  2. Press Cmd-Option-I to open DevTools. Choose the Network tab.
  3. Type  browse  in the filter box.
  4. Click "Library" in the app, so the page makes a request.
  5. Find a POST to  https://music.youtube.com/youtubei/v1/browse
  6. Right-click that request. Choose Copy, then "Copy as cURL".
  7. Run this command again. It reads the clipboard.

A page load will not work. Only that API call carries the cookie and the
x-goog-authuser header.

The clipboard is the default because a terminal cannot carry this text. One
line of it runs past 2000 characters, which is longer than a terminal accepts.

To read a file instead, use --from-file PATH. To read a pipe, use --from-file -

WARNING: this capture holds live account cookies. Keep it on the clipboard or
in a file you delete. Never paste it into a chat, an issue, or a message."""


class MissingHeadersError(ValueError):
    """The pasted text carries no usable credential. The message names what is absent."""


def parse_curl(text: str) -> dict[str, str]:
    """Return the headers of a cURL command, with lower-case names.

    `shlex` splits the text the way a shell does, so it handles the quoting Chrome writes and the
    backslash at the end of each line. A walk over the tokens keeps the request body out of the
    result, because `--data-raw` holds JSON that can look like a flag.
    """
    tokens = shlex.split(text)
    headers: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        value = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token in _HEADER_FLAGS and ": " in value:
            name, _, content = value.partition(": ")
            headers[name.strip().lower()] = content.strip()
            index += 2
            continue
        if token in _COOKIE_FLAGS and value:
            headers["cookie"] = value.strip()
            index += 2
            continue
        index += 1
    return headers


def parse_header_block(text: str) -> dict[str, str]:
    """Return the headers of a `name: value` block, with lower-case names."""
    headers: dict[str, str] = {}
    for line in text.splitlines():
        name, separator, content = line.partition(": ")
        if separator and not name.startswith(":"):
            headers[name.strip().lower()] = content.strip()
    return headers


def normalise(text: str) -> str:
    """Return `text` as a header block `ytmusicapi` accepts.

    Reads a cURL command or a header block. Raises `MissingHeadersError` naming every required
    header the text lacks, and naming the request that carries them.
    """
    stripped = text.strip()
    headers = parse_curl(stripped) if stripped.startswith("curl") else parse_header_block(stripped)

    missing = [name for name in _REQUIRED if not headers.get(name)]
    if missing:
        message = (
            f"the pasted text carries no {' and no '.join(missing)}. "
            "Copy a POST to https://music.youtube.com/youtubei/v1/browse while signed in. "
            "A page load carries neither."
        )
        raise MissingHeadersError(message)

    return "\n".join(f"{name}: {value}" for name, value in headers.items())
