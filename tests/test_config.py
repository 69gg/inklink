from pathlib import Path

import pytest
from pydantic import ValidationError

from inklink.config import (
    AppConfig,
    api_key_for_profile,
    client_options_for_profile,
    load_config,
    request_options_for_profile,
)


def test_load_minimal_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models.default]
api = "responses"
model = "gpt-test"
api_key_env = "OPENAI_API_KEY"

[tasks]
drafting = "default"
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.models["default"].model == "gpt-test"
    assert config.profile_for_task("review") == "default"
    assert config.profile_for_task("drafting") == "default"


def test_empty_optional_values_are_omitted(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models.default]
api = "responses"
model = "gpt-test"
api_key_env = "OPENAI_API_KEY"
base_url = ""
temperature = ""
""",
        encoding="utf-8",
    )
    profile = load_config(path).models["default"]
    assert "base_url" not in client_options_for_profile(profile)
    assert "temperature" not in request_options_for_profile(profile)


def test_profile_api_key_can_be_read_from_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models.default]
api = "responses"
model = "gpt-test"
api_key = "sk-from-config"
api_key_env = "OPENAI_API_KEY"
""",
        encoding="utf-8",
    )
    profile = load_config(path).models["default"]

    assert profile.api_key == "sk-from-config"
    assert api_key_for_profile(profile, {"OPENAI_API_KEY": "sk-from-env"}) == "sk-from-config"


def test_profile_api_key_falls_back_to_environment(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models.default]
api = "responses"
model = "gpt-test"
api_key = ""
api_key_env = "OPENAI_API_KEY"
""",
        encoding="utf-8",
    )
    profile = load_config(path).models["default"]

    assert profile.api_key is None
    assert api_key_for_profile(profile, {"OPENAI_API_KEY": "sk-from-env"}) == "sk-from-env"


def test_load_config_normalizes_blank_optional_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[writing]
retrieval_token_budget = ""

[models.default]
api = "responses"
model = "gpt-test"
api_key_env = "OPENAI_API_KEY"
base_url = ""
timeout_seconds = ""
temperature = ""
""",
        encoding="utf-8",
    )
    config = load_config(path)
    profile = config.models["default"]
    assert config.writing.retrieval_token_budget is None
    assert profile.base_url is None
    assert profile.timeout_seconds is None
    assert "temperature" not in request_options_for_profile(profile)


def test_client_options_are_separate_from_request_options() -> None:
    config = AppConfig.model_validate(
        {
            "models": {
                "default": {
                    "api": "responses",
                    "model": "gpt-test",
                    "api_key_env": "OPENAI_API_KEY",
                    "base_url": "https://example.test/v1",
                    "timeout_seconds": 30,
                }
            }
        }
    )
    profile = config.models["default"]
    assert client_options_for_profile(profile) == {
        "base_url": "https://example.test/v1",
        "timeout": 30.0,
        "max_retries": 2,
    }
    assert "base_url" not in request_options_for_profile(profile)
    assert "timeout" not in request_options_for_profile(profile)


def test_responses_request_options_use_responses_parameter_names() -> None:
    config = AppConfig.model_validate(
        {
            "models": {
                "default": {
                    "api": "responses",
                    "model": "gpt-test",
                    "reasoning_effort": "high",
                    "max_completion_tokens": 500,
                }
            }
        }
    )
    options = request_options_for_profile(config.models["default"])
    assert options["reasoning"] == {"effort": "high"}
    assert options["max_output_tokens"] == 500
    assert "reasoning_effort" not in options
    assert "max_completion_tokens" not in options


def test_chat_completions_request_options_use_chat_parameter_names() -> None:
    config = AppConfig.model_validate(
        {
            "models": {
                "default": {
                    "api": "chat_completions",
                    "model": "gpt-test",
                    "reasoning_effort": "high",
                    "max_completion_tokens": 500,
                }
            }
        }
    )
    options = request_options_for_profile(config.models["default"])
    assert options["reasoning_effort"] == "high"
    assert options["max_completion_tokens"] == 500
    assert "reasoning" not in options
    assert "max_output_tokens" not in options


def test_default_profile_is_required() -> None:
    with pytest.raises(ValidationError, match="default"):
        AppConfig.model_validate(
            {
                "models": {
                    "cheap": {
                        "api": "responses",
                        "model": "gpt-test",
                    }
                }
            }
        )


def test_task_profiles_must_exist() -> None:
    with pytest.raises(ValidationError, match="missing"):
        AppConfig.model_validate(
            {
                "models": {
                    "default": {
                        "api": "responses",
                        "model": "gpt-test",
                    }
                },
                "tasks": {"drafting": "missing"},
            }
        )


def test_profile_for_task_returns_configured_profile() -> None:
    config = AppConfig.model_validate(
        {
            "models": {
                "default": {
                    "api": "responses",
                    "model": "gpt-test",
                },
                "draft": {
                    "api": "chat_completions",
                    "model": "gpt-draft",
                },
            },
            "tasks": {"drafting": "draft"},
        }
    )
    assert config.profile_for_task("drafting") == "draft"
    assert config.profile_for_task("review") == "default"


def test_unknown_config_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        AppConfig.model_validate(
            {
                "models": {
                    "default": {
                        "api": "responses",
                        "model": "gpt-test",
                        "unexpected": True,
                    }
                }
            }
        )


def test_invalid_api_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "models": {
                    "default": {
                        "api": "completion",
                        "model": "gpt-test",
                    }
                }
            }
        )


def test_reasoning_effort_is_passed_through_without_enum_validation() -> None:
    config = AppConfig.model_validate(
        {
            "models": {
                "default": {
                    "api": "responses",
                    "model": "gpt-test",
                    "reasoning_effort": "provider-custom-effort",
                }
            }
        }
    )

    assert request_options_for_profile(config.models["default"])["reasoning"] == {
        "effort": "provider-custom-effort"
    }


def test_numeric_config_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "writing": {"word_count_tolerance_ratio": -0.1},
                "models": {
                    "default": {
                        "api": "responses",
                        "model": "gpt-test",
                        "max_concurrency": 0,
                    }
                },
            }
        )
