from __future__ import annotations

from collections.abc import Sequence
from typing import TypeGuard

from inklink.llm.types import NormalizedUsage

_MISSING = object()


def normalize_usage(raw: object) -> NormalizedUsage:
    return NormalizedUsage(
        input_tokens=_first_int(raw, ("input_tokens", "prompt_tokens")),
        output_tokens=_first_int(raw, ("output_tokens", "completion_tokens")),
        total_tokens=_first_int(raw, ("total_tokens",)),
        reasoning_tokens=_first_int(
            raw,
            ("reasoning_tokens",),
            ("output_tokens_details", "completion_tokens_details"),
        ),
        cached_tokens=_first_int(
            raw,
            ("cached_tokens",),
            ("input_tokens_details", "prompt_tokens_details"),
        ),
        cache_read_tokens=_first_int(
            raw,
            ("cache_read_tokens",),
            ("input_tokens_details", "prompt_tokens_details"),
        ),
        cache_write_tokens=_first_int(
            raw,
            ("cache_write_tokens",),
            ("input_tokens_details", "prompt_tokens_details"),
        ),
    )


def _first_int(
    raw: object,
    names: Sequence[str],
    detail_names: Sequence[str] = (),
) -> int | None:
    for name in names:
        value = _read(raw, name)
        if _is_int_token(value):
            return value

    for detail_name in detail_names:
        details = _read(raw, detail_name)
        for name in names:
            value = _read(details, name)
            if _is_int_token(value):
                return value

    return None


def _read(source: object, name: str) -> object:
    if source is None:
        return _MISSING
    if isinstance(source, dict):
        return source.get(name, _MISSING)
    return getattr(source, name, _MISSING)


def _is_int_token(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
