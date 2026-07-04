"""버전 SSoT(``__version__``)가 pyproject.toml의 project.version과 일치하는지 검증."""

from __future__ import annotations

import tomllib
from pathlib import Path

import naver_post_crawler


def test_version_matches_pyproject() -> None:
    # covers: Test-16
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert naver_post_crawler.__version__ == data["project"]["version"]
