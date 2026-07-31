"""Velopack 래퍼(src/naver_post_crawler/velopack_update.py) 검증.

실제 업데이트 동작은 Velopack 네이티브 라이브러리 몫이라 여기서 검증하지 않는다. 우리
코드로 남는 것은 얇은 래퍼뿐이고, 그 래퍼가 지켜야 할 계약은 셋이다.

1. **지연 임포트**: 모듈을 import해도 velopack 네이티브 모듈이 로드되지 않아야 한다
   (import만으로 0.5초 이상 걸려 첫 화면이 그만큼 늦어진다).
2. **진행률 환산**: velopack이 주는 0~100 정수를 GUI가 쓰는 0.0~1.0으로 바꾼다.
3. **예외 삼킴**: 업데이트 계층 실패가 앱 기동을 막으면 안 된다.
"""

from __future__ import annotations

import subprocess
import sys

from naver_post_crawler import velopack_update


def test_import_does_not_load_native_velopack() -> None:
    # covers: Test-17
    # 이미 다른 테스트가 velopack을 import했을 수 있으므로 깨끗한 인터프리터에서 확인한다.
    code = (
        "import sys; import naver_post_crawler.velopack_update as m; "
        "print('velopack' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False", (
        "velopack을 모듈 최상단에서 import하면 앱 첫 화면이 그만큼 늦어진다"
    )


def test_download_progress_is_normalized(monkeypatch) -> None:
    # covers: Test-18
    seen: list[float] = []
    reported: list[int] = [0, 50, 100]

    class _FakeManager:
        def download_updates(self, _info: object, cb) -> None:
            for percent in reported:
                cb(percent)

    monkeypatch.setattr(velopack_update, "_manager", lambda: _FakeManager())

    velopack_update.download(object(), seen.append)

    assert seen == [0.0, 0.5, 1.0]


def test_run_startup_maintenance_swallows_errors(monkeypatch) -> None:
    # covers: Test-19
    # velopack import 자체가 실패하는 상황(개발 실행)을 흉내 낸다.
    monkeypatch.setitem(sys.modules, "velopack", None)

    velopack_update.run_startup_maintenance()  # 예외가 새어 나오면 실패다


def test_is_installed_is_false_when_manager_raises(monkeypatch) -> None:
    # covers: Test-19
    def boom():
        raise RuntimeError("설치 컨텍스트가 아니다")

    monkeypatch.setattr(velopack_update, "_manager", boom)

    assert velopack_update.is_installed() is False
    assert velopack_update.current_version() is None


def test_target_version_falls_back_when_shape_differs() -> None:
    # covers: Test-19 (비공식 객체 구조에 의존하므로 모양이 달라도 죽지 않아야 한다)
    assert velopack_update.target_version(object()) == "?"
