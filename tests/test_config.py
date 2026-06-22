from pathlib import Path

from inklink.config import AppConfig, load_config, request_options_for_profile


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


def test_empty_optional_values_are_omitted() -> None:
    config = AppConfig.model_validate(
        {
            "models": {
                "default": {
                    "api": "responses",
                    "model": "gpt-test",
                    "api_key_env": "OPENAI_API_KEY",
                    "base_url": "",
                    "temperature": None,
                }
            }
        }
    )
    options = request_options_for_profile(config.models["default"])
    assert "base_url" not in options
    assert "temperature" not in options
