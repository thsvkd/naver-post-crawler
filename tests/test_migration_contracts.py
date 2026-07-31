"""이관이 끝난 뒤에도 유지되어야 하는 저장소 수준 계약.

개별 모듈 테스트로는 드러나지 않고, "다시 되돌아오면 조용히 아픈" 것들만 모았다.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[1]


def _load_build_script():
    spec = importlib.util.spec_from_file_location(
        "npc_build_script_contracts", _REPO_ROOT / "scripts" / "build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_pack_id_differs_from_app_data_dir_name() -> None:
    # covers: Test-28
    from naver_post_crawler import cookie

    build = _load_build_script()

    assert build.PACK_ID != cookie._APP_DIR, (
        "Windows 기본 설치 경로가 %LocalAppData%\\<PackId>\\ 이므로 두 이름이 같으면 "
        "언인스톨할 때 사용자 쿠키까지 함께 지워진다"
    )


def test_custom_updater_is_gone() -> None:
    # covers: Test-30
    assert not (_REPO_ROOT / "src" / "naver_post_crawler" / "updater.py").exists()
    assert not (_REPO_ROOT / "tests" / "test_updater.py").exists()

    offenders = []
    for directory in ("src", "scripts", "tests"):
        for path in sorted((_REPO_ROOT / directory).rglob("*.py")):
            if path.resolve() == Path(__file__).resolve():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "import updater" in line or "from .updater" in line:
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")

    assert offenders == [], f"커스텀 updater를 아직 import하는 곳이 있습니다: {offenders}"


@pytest.mark.parametrize("module", ["naver_post_crawler.cli", "naver_post_crawler.gui"])
def test_entrypoint_modules_import_cleanly(module: str) -> None:
    # covers: Test-30 (updater 제거 후 진입점이 그대로 살아 있는지)
    assert importlib.import_module(module) is not None
