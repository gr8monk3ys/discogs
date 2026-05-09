from pathlib import Path

import pytest
from click.testing import CliRunner

from discogs.cli.__main__ import cli


def test_auth_set_writes_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        cli, ["auth", "set"],
        input="my-secret-token\nlorenzo\n",
    )
    assert result.exit_code == 0, result.output

    config_path = tmp_path / ".discogs" / "config.toml"
    assert config_path.exists()
    contents = config_path.read_text()
    assert "my-secret-token" in contents
    assert 'username = "lorenzo"' in contents

    mode = config_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_auth_set_does_not_echo_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        cli, ["auth", "set"],
        input="hunter2\nlorenzo\n",
    )
    assert "hunter2" not in result.output
