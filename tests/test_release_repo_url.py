"""릴리스 저장소 URL 단일 출처 계약 테스트.

origin 은 ``https://github.com/thsvkd/naver-post-crawler.git`` 인데 옛 이름
(``naver-blog-crawler``)이 코드와 README 에 남아 있었다. GitHub API 가 301 리다이렉트로
받아 주고 있을 뿐이고, Velopack ``GithubSource`` 가 리다이렉트를 따라간다는 보장은
없다. 저장소 이름이 재사용되면 아예 다른 저장소의 릴리스를 보게 된다.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[1]

# origin 저장소 URL. 앱 모듈과 빌드 스크립트가 같은 값을 써야 한다.
_CANONICAL_REPO_URL = "https://github.com/thsvkd/naver-post-crawler"
# 더 이상 쓰지 않는 옛 저장소 이름.
_LEGACY_REPO_NAME = "naver-blog-crawler"

# 이 파일 자신은 옛 이름을 문자열로 들고 있으므로 검사 대상에서 제외한다.
_SELF = Path(__file__).resolve()


def _scanned_files() -> list[Path]:
    """옛 저장소 이름이 남아 있으면 안 되는 파일 목록."""
    files = [
        path
        for directory in ("src", "scripts", "tests")
        for path in sorted((_REPO_ROOT / directory).rglob("*.py"))
        if path.resolve() != _SELF
    ]
    files.append(_REPO_ROOT / "README.md")
    return files


def test_legacy_repo_name_is_absent_from_sources() -> None:
    # covers: Test-16
    offenders = []
    for path in _scanned_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _LEGACY_REPO_NAME in line:
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")

    assert offenders == [], f"옛 저장소 이름 {_LEGACY_REPO_NAME!r} 이 남아 있습니다: {offenders}"


def test_app_module_and_build_script_share_one_repo_url() -> None:
    # covers: Test-16
    # 앱 측 단일 출처는 velopack_update.REPO_URL 이다(W-11 에서 신설).
    assert importlib.util.find_spec("naver_post_crawler.velopack_update") is not None, (
        "naver_post_crawler.velopack_update 모듈이 없습니다(저장소 URL 단일 출처)."
    )
    velopack_update = importlib.import_module("naver_post_crawler.velopack_update")
    assert getattr(velopack_update, "REPO_URL", None) == _CANONICAL_REPO_URL

    # 빌드 스크립트는 그 값을 읽어 쓴다. 문자열로 중복해서 들고 있다면 최소한 값이 같아야 한다.
    build_source = (_REPO_ROOT / "scripts" / "build.py").read_text(encoding="utf-8")
    assert "REPO_URL" in build_source, "scripts/build.py 가 저장소 URL 을 참조하지 않습니다."
    literals = set(re.findall(r"https://github\.com/[\w.-]+/[\w.-]+", build_source))
    assert literals <= {_CANONICAL_REPO_URL}, (
        f"scripts/build.py 가 다른 저장소 URL 을 하드코딩하고 있습니다: {sorted(literals)}"
    )
