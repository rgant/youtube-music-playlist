"""The shape of the JSON this project reads, and never authors.

`bootstrap` reads the YouTube Data API and two credential files. `refresh` reads the YouTube Music
library. A third party owns every one of those inputs, so a field can be absent, or carry another
type, on any day. One alias covers them all, so `bootstrap` and `refresh` check the same shape the
same way.
"""

# A JSON value, recursively. Every branch stays concrete, and no branch falls back to `object` or
# `Any`. `isinstance` therefore narrows a field to a fully known type. A `typing.cast` asserts a
# type without a check, so no cast stands in for a check of a value typed this way.
#
# Public, and in a module of its own. `refresh.LibraryClient` returns `list[dict[str, JSON]]`, so a
# caller outside that module cannot implement the protocol or annotate a factory for it without
# this name. `bootstrap` needs the same name for the API responses it validates.
type JSON = bool | int | float | str | list[JSON] | dict[str, JSON] | None
