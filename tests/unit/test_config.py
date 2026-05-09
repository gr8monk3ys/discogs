import textwrap
from pathlib import Path

import pytest

from discogs.config import load_config


def test_load_config_reads_token_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(textwrap.dedent("""
        [discogs]
        token = "abc123"
        username = "lorenzo"
    """))

    cfg = load_config(config_path)

    assert cfg.discogs_token == "abc123"
    assert cfg.discogs_username == "lorenzo"


def test_env_token_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\ntoken = "from-file"\nusername = "u"')
    monkeypatch.setenv("DISCOGS_TOKEN", "from-env")

    cfg = load_config(config_path)

    assert cfg.discogs_token == "from-env"


def test_repr_redacts_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\ntoken = "supersecret"\nusername = "u"')

    cfg = load_config(config_path)
    rendered = repr(cfg)

    assert "supersecret" not in rendered
    assert "***" in rendered


def test_missing_file_raises_with_helpful_message(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="discogs auth set"):
        load_config(tmp_path / "does-not-exist.toml")


def test_missing_token_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\nusername = "u"')

    with pytest.raises(ValueError, match="token"):
        load_config(config_path)


def test_load_config_default_llm_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[discogs]\ntoken = "abc"\nusername = "u"')
    cfg = load_config(config_path)
    assert cfg.daily_llm_budget == 100
    assert cfg.influences_model == "claude-haiku-4-5-20251001"
    assert cfg.enrich_model == "claude-haiku-4-5-20251001"


def test_load_config_llm_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[discogs]\ntoken = "abc"\nusername = "u"\n'
        '[llm]\ndaily_budget = 250\n'
        'influences_model = "claude-sonnet-4-6"\n'
        'enrich_model = "claude-opus-4-7"\n'
    )
    cfg = load_config(config_path)
    assert cfg.daily_llm_budget == 250
    assert cfg.influences_model == "claude-sonnet-4-6"
    assert cfg.enrich_model == "claude-opus-4-7"
