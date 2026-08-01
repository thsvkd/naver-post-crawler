"""앱 내 웹뷰 네이버 로그인 — 세션 쿠키(HttpOnly 포함) 수거.

pywebview를 별도 헬퍼 서브프로세스로 띄워 사용자가 직접 로그인하게 하고, 그 웹뷰
엔진의 네이티브 쿠키 저장소에서 naver.com 세션 쿠키(``NID_AUT``/``NID_SES`` 등,
HttpOnly 포함)를 읽어 부모(GUI)로 돌려준다. ``NID_AUT``는 HttpOnly라 JS
``document.cookie``로 못 읽으므로 네이티브 쿠키 저장소 접근이 필수다.

Flet(serious_python)은 CPython을 Flutter와 한 프로세스에 내장해 진짜 메인 스레드를
Flutter가 점유한다. pywebview는 메인 스레드를 요구하므로 인프로세스로 띄울 수 없어
**별도 프로세스**로 실행한다. 부모는 앱을 헬퍼 모드로 재실행하고, 헬퍼는 결과 JSON을
지정된 파일 경로에 쓴다::

    {"status": "captured|timeout|error", "cookies": [{"name","value","domain"}, ...]}

.. important::
    헬퍼 모드와 결과 경로는 **환경변수로만** 전달한다(:data:`HELPER_ENV`,
    :data:`HELPER_RESULT_ENV`). 명령행 인자를 쓰면 안 된다 — ``flet build``가 만드는
    Flutter 러너의 Dart 진입점은 **인자가 하나라도 있으면 "개발자 모드"** 로 판정해 그
    인자를 페이지 URL로 해석하고 파이썬을 아예 실행하지 않는다. 즉 배포본에서 앱을 인자와
    함께 재실행하면 로그인 창 대신 빈 창이 뜨고 헬퍼는 영영 결과를 쓰지 못한다.
    같은 이유로 헬퍼 진입 판정(:func:`is_helper_mode`)도 ``sys.argv``를 보지 않는다.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .cookie import format_cookie_header

# 부모가 헬퍼 모드로 재실행할 때 세우는 환경변수와 그 값. gui.main()이 이걸 보고 분기한다.
# argv를 쓰지 않는 이유는 모듈 docstring 참고.
HELPER_ENV = "NAVER_POST_CRAWLER_MODE"
HELPER_MODE = "cookie-login-helper"
# 헬퍼가 결과 JSON을 써야 할 파일 경로를 넘기는 환경변수.
HELPER_RESULT_ENV = "NAVER_POST_CRAWLER_COOKIE_RESULT"

# 네이버 로그인 페이지. 로그인 성공 후 naver.com으로 리다이렉트된다.
_LOGIN_URL = "https://nid.naver.com/nidlogin.login"
# 이 쿠키가 보이면 로그인 세션이 성립한 것으로 본다(HttpOnly).
_SESSION_MARKER = "NID_AUT"
# 로그인 대기 상한(초). 넘으면 timeout으로 종료한다.
_LOGIN_TIMEOUT_S = 300
# 로그인 감지 폴링 주기(초). loaded 이벤트가 SPA 리다이렉트를 놓칠 때의 보강.
_POLL_INTERVAL_S = 2.0


def normalize_cookies(raw: object) -> list[tuple[str, str, str]]:
    """pywebview ``get_cookies()`` 반환을 ``(name, value, domain)`` 삼중쌍으로 정규화한다.

    pywebview 6.2.1은 쿠키당 ``http.cookies.SimpleCookie`` 하나를 담은 리스트를 준다
    (각 SimpleCookie는 ``name -> Morsel``, Morsel은 ``.value``·``["domain"]`` 보유).
    버전/백엔드에 따라 ``http.cookiejar.Cookie`` 형태일 수도 있어 둘 다 처리한다.
    """
    out: list[tuple[str, str, str]] = []
    for item in raw or []:  # type: ignore[union-attr]
        if hasattr(item, "items"):  # SimpleCookie: name -> Morsel
            for name, morsel in item.items():
                out.append((name, morsel.value, morsel["domain"]))
        elif hasattr(item, "name") and hasattr(item, "value"):  # http.cookiejar.Cookie
            out.append((item.name, item.value, getattr(item, "domain", "") or ""))
    return out


def parse_helper_output(returncode: int, stdout: str) -> str | None:
    """헬퍼 결과(JSON 문자열)를 naver 쿠키 헤더 문자열로 파싱한다(실패 시 ``None``).

    로그인이 성립하지 않았거나(타임아웃/``NID_AUT`` 없음), 헬퍼가 비정상 종료했거나,
    출력이 유효한 JSON이 아니면 ``None``을 돌려준다(예외 없이 부모 크래시 방지).
    """
    if returncode != 0:
        return None
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") != "captured":
        return None
    triples = [
        (str(c.get("name", "")), str(c.get("value", "")), str(c.get("domain", "")))
        for c in data.get("cookies") or []
        if isinstance(c, dict)
    ]
    # NID_AUT가 없으면 로그인 세션이 아니므로 로그인 미완료로 취급한다.
    if not any(name == _SESSION_MARKER for name, _, _ in triples):
        return None
    return format_cookie_header(triples) or None


def _is_bundled() -> bool:
    """``flet build`` 배포본에서 도는지 판정한다.

    러너가 프로덕션 실행에서 세우는 ``FLET_APP_STORAGE_DATA``를 신호로 쓴다.
    ``sys.frozen``은 PyInstaller가 세우는 값이라 ``flet build`` 번들에서는 서지 않는다 —
    그걸 근거로 쓰면 배포본이 개발용 분기(``python -m ...``)를 타고 실패한다.
    """
    return bool(os.environ.get("FLET_APP_STORAGE_DATA"))


def _helper_command() -> list[str]:
    """헬퍼 서브프로세스 실행 커맨드.

    **인자를 붙이지 않는다.** 헬퍼 모드와 결과 경로는 :func:`helper_env`가 만드는
    환경변수로 넘어간다(이유는 모듈 docstring 참고).
    """
    if _is_bundled():
        # 배포본: 앱 실행 파일 자신을 인자 없이 재실행한다.
        return [sys.executable]
    # 개발 실행: 패키지를 모듈로 실행한다(python -m naver_post_crawler).
    return [sys.executable, "-m", "naver_post_crawler"]


def helper_env(result_path: Path) -> dict[str, str]:
    """헬퍼에 넘길 환경변수 사전.

    현재 환경을 그대로 상속한 뒤 헬퍼 모드 표시와 결과 파일 경로를 얹는다. 상속하지 않으면
    ``PATH``·프록시·디스플레이 설정이 사라져 헬퍼가 아예 뜨지 못한다.
    """
    return {**os.environ, HELPER_ENV: HELPER_MODE, HELPER_RESULT_ENV: str(result_path)}


def is_helper_mode() -> bool:
    """이 프로세스가 헬퍼 모드로 기동됐는지. 판정 근거는 환경변수뿐이다(``sys.argv`` 아님)."""
    return os.environ.get(HELPER_ENV) == HELPER_MODE


def login_and_capture(runner: Callable[..., object] = subprocess.run) -> str | None:
    """헬퍼 서브프로세스를 실행해 로그인 세션 쿠키 헤더를 얻는다(없으면 ``None``).

    ``runner``는 테스트에서 주입할 수 있게 열어 둔다(기본 :func:`subprocess.run`).
    헬퍼는 결과를 임시 파일에 쓰므로, webview가 stdout에 남길 수 있는 잡음과 분리된다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result_path = Path(tmp) / "cookies.json"
        try:
            # 헬퍼가 스스로 끝내지 못해도(교착 등) 부모가 무한 대기하지 않게 상한을 둔다.
            # 헬퍼 자체 타임아웃보다 넉넉히 크게 잡고, 초과 시 subprocess.run이 자식을 종료한다.
            proc = runner(
                _helper_command(),
                capture_output=True,
                text=True,
                timeout=_LOGIN_TIMEOUT_S + 30,
                env=helper_env(result_path),
            )
        except subprocess.TimeoutExpired:
            return None
        try:
            output = result_path.read_text(encoding="utf-8")
        except OSError:
            output = ""
        finally:
            # 읽자마자 지운다. 디렉터리 정리(컨텍스트 종료)에만 맡기면, 그 사이에 부모가
            # 죽었을 때 세션 쿠키가 평문으로 임시 디렉터리에 남는다. Windows %TEMP%는
            # 자동 정리가 사실상 없고 macOS는 재부팅 때만 정리한다.
            with contextlib.suppress(OSError):
                result_path.unlink()
    return parse_helper_output(proc.returncode, output)  # type: ignore[attr-defined]


def _write_result(out_path: Path, status: str, triples: list[tuple[str, str, str]]) -> None:
    """헬퍼 결과 JSON을 **소유자 전용 권한으로** 쓴다.

    이 파일에는 세션 쿠키가 평문으로 담긴다. ``write_text``는 0644에서 umask를 뺀 권한으로
    만들므로 같은 기기의 다른 사용자에게 열려 있을 수 있다. 부모가 만든 임시 디렉터리가
    0700이라 지금은 가려지지만, 디렉터리 권한에 기대지 않고 파일 자체를 좁힌다.
    ``O_EXCL``은 같은 경로가 미리 놓여 있을 때 그것을 덮어쓰지 않게 한다.
    """
    payload = json.dumps(
        {
            "status": status,
            "cookies": [{"name": n, "value": v, "domain": d} for n, v, d in triples],
        },
        ensure_ascii=False,
    )
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(payload)


def run_helper(result_path: str | None = None) -> int:
    """헬퍼 프로세스 본체 — pywebview로 로그인 창을 띄우고 쿠키를 수거해 파일에 쓴다.

    ``NID_AUT``가 보이면 즉시 수거하고 창을 닫는다. 시간 내 로그인이 없으면 timeout.
    pywebview는 여기서만 지연 임포트한다(앱 전체가 pywebview에 하드 의존하지 않도록).
    """
    import webview  # 지연 임포트: 헬퍼 모드에서만 필요하다.

    if result_path is None:
        # 부모가 환경변수로 넘긴다(argv를 쓰면 배포본에서 파이썬이 아예 안 뜬다).
        result_path = os.environ.get(HELPER_RESULT_ENV) or "cookies.json"
    out_path = Path(result_path)

    done = threading.Event()
    lock = threading.Lock()

    def write(status: str, triples: list[tuple[str, str, str]]) -> None:
        _write_result(out_path, status, triples)

    window = webview.create_window(
        "네이버 로그인",
        _LOGIN_URL,
        width=520,
        height=780,
    )

    def capture() -> bool:
        """세션 쿠키가 보이면 수거·기록하고 창을 닫는다. 아직이면 False.

        ``get_cookies()``는 GUI 스레드로 마샬링되는 블로킹 호출이라, lock을 쥔 채
        부르면 GUI 스레드에서 도는 다른 capture()와 교착할 수 있다. 쿠키 수거는 락
        밖에서 하고, 락은 done 판정·기록에만 짧게 잡는다.
        """
        if done.is_set():
            return True
        try:
            triples = normalize_cookies(window.get_cookies())
        except Exception:
            return False
        if not any(name == _SESSION_MARKER for name, _, _ in triples):
            return False
        with lock:
            if done.is_set():
                return True
            write("captured", triples)
            done.set()
        window.destroy()
        return True

    def on_loaded(*_args: object) -> None:
        # 로그인 페이지에는 NID_AUT가 없다 → capture가 False로 무시하고, 성립 후 로드에서 수거.
        # get_cookies가 GUI 스레드를 블로킹하지 않도록 별도 스레드에서 수거한다.
        threading.Thread(target=capture, daemon=True).start()

    def poll() -> None:
        while not done.wait(_POLL_INTERVAL_S):
            if capture():
                return

    def timeout() -> None:
        if done.wait(_LOGIN_TIMEOUT_S):
            return
        with lock:
            if done.is_set():
                return
            write("timeout", [])
            done.set()
        with contextlib.suppress(Exception):
            window.destroy()

    window.events.loaded += on_loaded
    threading.Thread(target=poll, daemon=True).start()
    threading.Thread(target=timeout, daemon=True).start()
    # private_mode(기본값)로 로그인 세션을 디스크에 영속하지 않는다(인메모리). 창이
    # 닫힐 때까지 블록하며 메인 스레드를 점유한다.
    webview.start(private_mode=True)
    return 0
