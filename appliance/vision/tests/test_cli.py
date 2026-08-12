from __future__ import annotations

from pathlib import Path

import pytest

from cs71vision.cli import main


def test_check_config_validates_the_development_defaults(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--check-config"]) == 0

    assert "configuration valid" in capsys.readouterr().out


def test_the_entry_point_requires_a_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])

    assert "required" in capsys.readouterr().err


def test_check_config_reports_an_invalid_configuration_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = tmp_path / "vision.toml"
    document.write_text('[vision]\nprofile = "not-a-profile"\nbackend = "fixture"\n')

    assert main(["--check-config", str(document)]) == 2
    assert "invalid configuration" in capsys.readouterr().err
