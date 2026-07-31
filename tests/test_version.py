"""버전 SSoT 테스트.

``flet build``는 앱을 site-packages 에 정식 설치하지 않고 ``src/``를 그대로 복사하므로
배포본 안에서 ``importlib.metadata`` 로 버전을 읽을 수 없다. 그래서 패키지의
``__version__`` 은 사람이 손으로 고치는 값이 아니라 ``scripts/_common.py`` 의
``sync_version()`` 이 pyproject.toml 로부터 만들어 내는 생성물이다(D-8). 여기서는
(1) pyproject.toml 과 ``__version__`` 의 일치와 (2) 그 생성물이 최신인지를 검증한다.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import _common

import naver_post_crawler

_REPO_ROOT = Path(__file__).parents[1]
_INIT_PATH = _REPO_ROOT / "src" / "naver_post_crawler" / "__init__.py"


def _pyproject_version() -> str:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_version_matches_pyproject() -> None:
    # covers: Test-31
    assert naver_post_crawler.__version__ == _pyproject_version()


def test_sync_version_keeps_generated_init_up_to_date() -> None:
    # covers: Test-31
    original = _INIT_PATH.read_text(encoding="utf-8")
    try:
        # 이미 최신인 생성물에 다시 돌리면 아무것도 바뀌지 않아야 한다.
        returned = _common.sync_version()
        after_noop = _INIT_PATH.read_text(encoding="utf-8")
        # 버전을 인위적으로 어긋나게 만든 뒤 다시 돌리면 pyproject 값으로 복구해야 한다
        # (생성 대상 파일을 실제로 이 패키지의 __init__.py 로 잡고 있는지도 함께 잠근다).
        _INIT_PATH.write_text(
            original.replace(f'__version__ = "{returned}"', '__version__ = "0.0.0"'),
            encoding="utf-8",
        )
        _common.sync_version()
        after_resync = _INIT_PATH.read_text(encoding="utf-8")
    finally:
        _INIT_PATH.write_text(original, encoding="utf-8")

    assert returned == _pyproject_version()
    assert after_noop == original
    assert after_resync == original
