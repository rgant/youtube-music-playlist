"""Tests for youtube_music_library_radio.jsonshape.

`bootstrap` and `refresh` both import `JSON` inside `if typing.TYPE_CHECKING:`, because both use it
in annotations alone. This module imports it at run time instead, which is the one place that proves
the shared alias resolves outside a type checker.
"""

import typing

from youtube_music_library_radio.jsonshape import JSON

# The one definition this project accepts for a JSON value, member by member and in order.
_DEFINITION = "bool | int | float | str | list[JSON] | dict[str, JSON] | None"


def test_the_json_alias_resolves_at_run_time_to_the_declared_definition() -> None:
    """`JSON` evaluates to the declared union, with every member and in the declared order.

    A PEP 695 `type` alias evaluates its value on first access alone. A recursive alias that names a
    type wrongly therefore fails at that access and nowhere earlier, so this test reads the value.

    A dropped member breaks every check that reads this alias. Without `float`, a response field
    that holds `1.5` narrows to no known type. The reader then rejects a value the API is free to
    send. A reordered union is the same type, and the order is the project's house standard, so this
    test locks it too.
    """
    # `TypeAliasType.__value__` is typed `Any`. The cast narrows it, and `str` reads the result.
    definition = typing.cast("object", JSON.__value__)

    assert str(definition) == _DEFINITION
