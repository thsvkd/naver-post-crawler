"""제거 안내 문서 계약 테스트.

macOS에는 제거 훅이 없다. ``.pkg``는 언인스톨러를 만들지 않고, 휴지통 드래그는 우리 코드가
실행될 기회를 주지 않는다. 그래서 **문서가 유일하게 동작하는 수단**이다. 경로가 하나라도
틀리면 사용자는 지웠다고 믿은 채 자격증명 항목을 남기게 된다.

경로는 빌드 설정(``scripts/build.py``의 조직 ID)에서 파생되므로, 문서에 박힌 값이 빌드와
어긋나지 않는지 여기서 고정한다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[1]
_README = _REPO_ROOT / "README.md"


def _load_build():
    spec = importlib.util.spec_from_file_location(
        "npc_build_script_docs", _REPO_ROOT / "scripts" / "build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _readme() -> str:
    return _README.read_text(encoding="utf-8")


def test_readme_has_an_uninstall_section() -> None:
    # covers: Test-16
    assert re.search(r"^#+ .*제거", _readme(), re.MULTILINE), (
        "README에 제거 섹션이 없다 — macOS에서는 문서가 유일한 정리 수단이다"
    )


def test_readme_documents_the_macos_data_path_from_build_config() -> None:
    # covers: Test-16
    # 앱 데이터 폴더 이름은 번들 ID이고, 번들 ID는 build.py의 조직 ID에서 파생된다.
    build = _load_build()
    bundle_id = f"{build._ORG}.naver-post-crawler"

    assert bundle_id in _readme(), (
        f"README의 macOS 데이터 경로가 빌드 설정과 어긋난다(기대: {bundle_id})"
    )


def test_readme_documents_leftovers_that_no_hook_can_remove() -> None:
    # covers: Test-16
    readme = _readme()

    # 홈 디렉터리 설치는 --volume이 필요해 명령 형태가 갈린다. 두 토큰으로 확인한다.
    assert "pkgutil" in readme and "--forget" in readme, "pkg 영수증 정리 방법이 없다"
    assert "키체인" in readme, "macOS 키체인 항목이 남는다는 안내가 없다"
    assert "delete-generic-password" in readme, "키체인 항목 삭제 명령이 없다"


def test_readme_documents_the_windows_removal_entry_point() -> None:
    # covers: Test-16
    readme = _readme()
    section = readme[readme.index("제거") :]

    assert "설정" in section and "앱" in section, "Windows 표준 제거 경로 안내가 없다"


def test_readme_keychain_command_matches_the_names_actually_used() -> None:
    # covers: Test-16
    # macOS 데이터 경로는 빌드 설정과 대조하면서 자격증명 이름만 빠뜨리면, SERVICE를
    # 바꾸는 순간 문서가 조용히 거짓이 된다. 사용자는 지웠다고 믿은 채 항목을 남긴다.
    from naver_post_crawler import credentials

    match = re.search(r"security delete-generic-password\s+-s\s+(\S+)\s+-a\s+(\S+)", _readme())

    assert match is not None, "README에 키체인 삭제 명령이 없다"
    assert match.group(1) == credentials.SERVICE
    assert match.group(2) == credentials.ACCOUNT
