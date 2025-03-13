# Everything in my Library Playlist

The purpose of this script is to create a playlist in YouTube Music that contains
all of the songs in my YouTube Music Library. Since Google doesn't have a way to
do that and I want to listen to everything on my Sonos 1 device.

## Login to YouTube Music

```sh
pipenv run ytmusicapi oauth
```

And follow instructions. This command should create a `oauth.json` file in the
current directory that can be used by `ytmusicapi` to modify my YouTube Music
account.

## Python Formatting

Use [Black](https://black.vercel.app/?version=stable&state=_Td6WFoAAATm1rRGAgAhARYAAAB0L-Wj4ABrAEddAD2IimZxl1N_W1ktIvcnCRywToX8gFKcWutDOwKKLKrASR9hIKEm62fqgvXlOZubtjviIJdrWHeOg9Eh_fA8IcOitKvsHsAAAADHH5AiHeIXpAABY2w8oFIqH7bzfQEAAAAABFla)
to format python code files. Settings should target the "stable" version, a line
length of 100 characters, python version 3.10+, "Don't normalize string quotes or
prefixes" (I prefer `"""Docstrings"""` and 'single quoted' string values.), and
"Format typing stubs" options.

Although it is possible to setup your editor to automatically format the code I
have not done so because I still find cases where automatic formatters mangle the
code. So I use the online sandbox linked above and then ignore some of the
suggestions that I find ugly. This figures out some better white space solutions
in python that I cannot always remember so I find it helpful.

## Silly Limits

It appears that YouTube Music will only allow 5,000 songs in a playlist. So in
order to have all of my music and playlists I will need multiple lists. It also
seems that Sonos will only load 500 tracks from a playlist that contains 5,000.
Possibly by using [SoCo library](https://soco.readthedocs.io/en/latest/api/soco.core.html)
I might be able to add more tracks to the queue manually. I believe I've seen
online that the maximum Sonos queue size is 40,000 tracks.
