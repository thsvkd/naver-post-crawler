"""Velopack 기반 설치/자동 업데이트 통합.

Velopack(Squirrel 후속)이 설치와 자동 업데이트를 함께 담당한다. 설치본은 Windows에서
``%LocalAppData%\\NaverPostCrawler\\current\\``에, macOS에서 ``~/Applications``(또는
``/Applications``)의 ``.app`` 번들로 놓이고, 업데이트는 GitHub Releases의 채널별 피드
(``releases.win.json`` / ``releases.osx.json``)와 nupkg(델타 우선)로 받는다.

이 모듈은 velopack 바인딩을 감싼 얇은 계층이다. velopack이 없는 개발 실행이나 설치
컨텍스트가 아닌 실행에서는 모든 함수가 안전하게 no-op / False로 떨어져 앱 기동을 막지
않는다.

설치/업데이트/제거 라이프사이클 훅(``--veloapp-*``)은 이 모듈이 다루지 않는다. flet이
만드는 Flutter 러너가 명령행 인자를 "개발자 모드"로 해석해 파이썬을 실행조차 하지 않기
때문에, 훅은 네이티브 진입점에서 처리한다(``scripts/flet_template.py`` 참고).

.. important::
    velopack은 네이티브 확장이라 **import만으로 0.5초 이상** 걸린다. 그래서 모든 함수가
    함수 안에서 지연 임포트하며, 네트워크를 타는 함수는 호출자가 워커 스레드에서 돌린다.
    모듈 최상단에서 import하면 그만큼 첫 화면이 늦어진다.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# GitHub Releases 소스. 릴리스 에셋에 releases.{win,osx}.json + *.nupkg가 올라가 있어야 한다.
# 앱과 빌드 스크립트가 공유하는 저장소 URL의 단일 출처다(scripts/build.py가 이 값을 읽는다).
REPO_URL = "https://github.com/thsvkd/naver-post-crawler"


def run_startup_maintenance() -> None:
    """velopack ``App().run()`` — 설치본 유지보수. **워커 스레드에서** 호출한다.

    라이프사이클 훅은 네이티브 러너가 이미 처리하므로(모듈 docstring 참고) 여기서 걸리는
    훅은 없다. 그래도 이 호출이 필요한 이유는 ``App().run()``이 다음 일도 같이 하기 때문이다.

    - **packages 폴더의 오래된 nupkg 삭제.** velopack에서 이 정리를 하는 곳은 여기뿐이다.
      부르지 않으면 업데이트할 때마다 이전 전체 패키지가 그대로 쌓인다.
    - 받아 두고 아직 적용하지 않은 업데이트가 있으면 적용 후 재시작.

    비설치/개발 실행이면 조용히 no-op이며, 어떤 예외도 앱 동작을 막지 않는다.
    """
    try:
        from velopack import App

        App().run()
    except Exception:  # noqa: BLE001 - 업데이트 계층 실패가 앱 동작을 막으면 안 된다.
        logger.debug("velopack 시작 유지보수 건너뜀(미설치/개발 실행)", exc_info=True)


_manager_cache = None


def _manager():
    """``UpdateManager``를 만들어 캐시한다(velopack은 여기서 지연 임포트한다)."""
    global _manager_cache
    if _manager_cache is None:
        from velopack import GithubSource, UpdateManager

        _manager_cache = UpdateManager(GithubSource(REPO_URL))
    return _manager_cache


def is_installed() -> bool:
    """Velopack 설치본에서 실행 중인지. 개발/비설치 실행이면 False.

    설치 메타데이터가 없으면 ``get_current_version``이 실패하므로 그걸 가드로 쓴다.
    """
    try:
        _manager().get_current_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def current_version() -> str | None:
    """Velopack이 인식하는 현재 설치 버전(비설치면 None)."""
    try:
        return _manager().get_current_version()
    except Exception:  # noqa: BLE001
        return None


def check():
    """업데이트가 있으면 ``UpdateInfo``, 없으면 None.

    GitHub로 네트워크 호출이 일어나므로 워커 스레드에서 호출한다. 네트워크/컨텍스트 오류는
    그대로 올려 호출자가 처리한다(GUI가 상태 메시지로 표시).
    """
    return _manager().check_for_updates()


def target_version(info) -> str:
    """``UpdateInfo``가 가리키는 대상 버전 문자열(알 수 없으면 ``"?"``)."""
    try:
        return info.TargetFullRelease.Version
    except Exception:  # noqa: BLE001
        return "?"


def download(info, progress_cb: Callable[[float], None] | None = None) -> None:
    """업데이트(델타 우선)를 로컬로 내려받는다(아직 적용하지 않음).

    ``progress_cb``는 0.0~1.0 진행률을 받는다(velopack은 0~100 정수를 주므로 환산).
    """
    cb = None
    if progress_cb is not None:

        def cb(percent):  # velopack: 0~100 int
            # 진행률 표시 실패가 다운로드를 멈추게 하면 안 된다.
            with contextlib.suppress(Exception):
                progress_cb(max(0.0, min(1.0, float(percent) / 100.0)))

    _manager().download_updates(info, cb)


def apply_and_restart(info) -> None:
    """받아 둔 업데이트를 적용하고 앱을 재시작한다 — 이 호출로 프로세스가 종료된다.

    :func:`download`를 먼저 마친 뒤 호출하며, 호출 전 사용자 안내를 끝내야 한다.
    """
    _manager().apply_updates_and_restart(info)
