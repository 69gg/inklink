from __future__ import annotations

from types import SimpleNamespace

from pydantic import ValidationError

from inklink.llm.types import NormalizedUsage
from inklink.llm.usage import normalize_usage


def test_chat_completions_usage_is_normalized() -> None:
    raw_usage = {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 4},
        "completion_tokens_details": {"reasoning_tokens": 3},
    }

    usage = normalize_usage(raw_usage)

    assert usage == NormalizedUsage(
        input_tokens=12,
        output_tokens=8,
        total_tokens=20,
        reasoning_tokens=3,
        cached_tokens=4,
    )


def test_responses_usage_is_normalized_from_object_attributes() -> None:
    raw_usage = SimpleNamespace(
        input_tokens=15,
        output_tokens=9,
        total_tokens=24,
        input_tokens_details=SimpleNamespace(cached_tokens=5),
        output_tokens_details=SimpleNamespace(reasoning_tokens=2),
    )

    usage = normalize_usage(raw_usage)

    assert usage == NormalizedUsage(
        input_tokens=15,
        output_tokens=9,
        total_tokens=24,
        reasoning_tokens=2,
        cached_tokens=5,
    )


def test_cache_read_and_write_tokens_are_normalized_from_details_and_top_level() -> None:
    usage_from_details = normalize_usage(
        {
            "input_tokens_details": {
                "cache_read_tokens": 6,
                "cache_write_tokens": 7,
            }
        }
    )
    usage_from_top_level = normalize_usage(
        {
            "cache_read_tokens": 8,
            "cache_write_tokens": 9,
        }
    )

    assert usage_from_details.cache_read_tokens == 6
    assert usage_from_details.cache_write_tokens == 7
    assert usage_from_top_level.cache_read_tokens == 8
    assert usage_from_top_level.cache_write_tokens == 9


def test_non_mapping_non_attribute_usage_returns_empty_usage() -> None:
    assert normalize_usage(123) == NormalizedUsage()
    assert normalize_usage(None) == NormalizedUsage()


def test_bool_token_values_are_ignored() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": True,
            "completion_tokens": False,
            "total_tokens": True,
            "prompt_tokens_details": {"cached_tokens": False},
            "completion_tokens_details": {"reasoning_tokens": True},
        }
    )

    assert usage == NormalizedUsage()


def test_zero_token_values_are_preserved() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_tokens_details": {
                "cached_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            },
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )

    assert usage == NormalizedUsage(
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        reasoning_tokens=0,
        cached_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )


def test_normalized_usage_rejects_extra_fields() -> None:
    try:
        NormalizedUsage(input_tokens=1, unexpected=2)
    except ValidationError as exc:
        assert "extra" in str(exc)
    else:
        raise AssertionError("NormalizedUsage accepted an extra field")
