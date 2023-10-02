from typing import Any, Callable, Optional

from ytmusicapi.navigation import nav as nav


def get_continuations(
    results: dict[str, str],
    continuation_type: str,
    limit: int | None,
    request_func: Callable[[str], dict[str, Any]],
    parse_func: Callable[[dict[str, str]], list[str]],
    ctoken_path: str = '',
    reloadable: bool = False,
) -> list[str]: ...
def get_validated_continuations(results, continuation_type, limit, per_page, request_func, parse_func, ctoken_path: str = ...): ...
def get_parsed_continuation_items(response, parse_func, continuation_type): ...
def get_continuation_params(results, ctoken_path: str = ...): ...
def get_reloadable_continuation_params(results): ...
def get_continuation_string(ctoken): ...
def get_continuation_contents(continuation, parse_func): ...
def resend_request_until_parsed_response_is_valid(request_func, request_additional_params, parse_func, validate_func, max_retries): ...
def validate_response(response, per_page, limit, current_count): ...
