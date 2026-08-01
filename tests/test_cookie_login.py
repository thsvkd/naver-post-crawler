"""웹뷰 헬퍼 쿠키 정규화 및 부모↔헬퍼 계약 파서 테스트.

pywebview 6.2.1 스파이크 실증(핸드오프 §4)에 따르면 ``get_cookies()``는
``list[http.cookies.SimpleCookie]``를 돌려주고, 헬퍼 서브프로세스는 결과를 stdout에
JSON 한 줄로 반환한다(핸드오프 §2). 이 두 계약을 검증한다.

여기에 더해 ``flet build`` 배포본을 위한 헬퍼 기동 계약(W-4)을 잠근다. flet 러너의
Dart 진입점은 **명령행 인자가 하나라도 있으면 개발자 모드**로 판정해 파이썬을 아예
실행하지 않으므로, 헬퍼 모드와 결과 파일 경로를 argv가 아니라 환경변수로 넘겨야 한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

import pytest

import naver_post_crawler.cookie_login as cookie_login_mod
from naver_post_crawler.cookie import format_cookie_header
from naver_post_crawler.cookie_login import (
    login_and_capture,
    normalize_cookies,
    parse_helper_output,
)

# 재설계 이전(argv 방식)에 쓰던 헬퍼 플래그. 상수 자체는 사라질 수 있으므로 임포트하지
# 않고 문자열로 고정해 둔다 — "이 문자열이 커맨드에 다시 나타나면 안 된다"가 계약이다.
_LEGACY_HELPER_FLAG = "--__cookie-login"

# W-4가 신설할 이름들. 아직 없을 때 모듈 수집 자체가 깨지지 않도록 모듈 속성으로 접근한다
# (수집 오류는 "무엇을 단언하려 했는지"를 가린다).
_HELPER_ENV = "HELPER_ENV"  # 헬퍼 모드를 알리는 환경변수 이름을 담은 상수의 이름
_HELPER_MODE = "HELPER_MODE"  # 그 환경변수에 넣을 값
_HELPER_RESULT_ENV = "HELPER_RESULT_ENV"  # 결과 파일 경로를 넘기는 환경변수 이름


def _const(name: str) -> str:
    """``cookie_login``이 정의해야 할 상수를 읽는다(없으면 그 사실이 그대로 실패로 드러난다)."""
    assert hasattr(cookie_login_mod, name), (
        f"cookie_login에 {name} 상수가 있어야 한다 — 헬퍼 모드는 환경변수로 전달한다(W-4)."
    )
    return getattr(cookie_login_mod, name)


def _simple_cookie(name: str, value: str, domain: str) -> SimpleCookie:
    """pywebview의 ``get_cookies()``가 돌려주는 형태 — 쿠키 1개당 SimpleCookie 1개."""
    sc: SimpleCookie = SimpleCookie()
    sc[name] = value
    sc[name]["domain"] = domain
    return sc


# -- normalize_cookies: pywebview SimpleCookie 리스트 -> (name, value, domain) 삼중쌍 ---


def test_normalize_cookies_converts_simplecookie_list_to_triples() -> None:
    # covers: Test-3
    raw = [
        _simple_cookie("NID_AUT", "AUTVALUE", ".naver.com"),
        _simple_cookie("NID_SES", "SESVALUE", ".naver.com"),
    ]

    assert normalize_cookies(raw) == [
        ("NID_AUT", "AUTVALUE", ".naver.com"),
        ("NID_SES", "SESVALUE", ".naver.com"),
    ]


def test_normalize_cookies_empty_input_returns_empty_list() -> None:
    # covers: Test-3
    assert normalize_cookies([]) == []


# -- parse_helper_output: 헬퍼 stdout(JSON 한 줄) -> naver 헤더 문자열 or None -----------


def test_parse_helper_output_captured_returns_naver_header() -> None:
    # covers: Test-4
    cookies = [
        {"name": "NID_AUT", "value": "AUTVALUE", "domain": ".naver.com"},
        {"name": "NID_SES", "value": "SESVALUE", "domain": ".naver.com"},
        {"name": "OTHER", "value": "X", "domain": ".google.com"},  # naver 아님 -> 제외
    ]
    stdout = json.dumps({"status": "captured", "cookies": cookies})
    expected = format_cookie_header([(c["name"], c["value"], c["domain"]) for c in cookies])

    result = parse_helper_output(0, stdout)

    assert result == expected
    assert result == "NID_AUT=AUTVALUE; NID_SES=SESVALUE"


def test_parse_helper_output_returns_none_when_status_timeout() -> None:
    # covers: Test-5
    stdout = json.dumps({"status": "timeout", "cookies": []})
    assert parse_helper_output(0, stdout) is None


def test_parse_helper_output_returns_none_when_captured_without_nid_aut() -> None:
    # covers: Test-5 (status는 captured여도 NID_AUT가 없으면 로그인 미완료로 취급)
    stdout = json.dumps(
        {
            "status": "captured",
            "cookies": [{"name": "NID_SES", "value": "SESVALUE", "domain": ".naver.com"}],
        }
    )
    assert parse_helper_output(0, stdout) is None


def test_parse_helper_output_returns_none_for_nonzero_returncode() -> None:
    # covers: Test-6 (헬퍼 프로세스 자체가 실패 종료한 경우 — 내용이 유효해도 무시)
    stdout = json.dumps(
        {
            "status": "captured",
            "cookies": [{"name": "NID_AUT", "value": "A", "domain": ".naver.com"}],
        }
    )
    assert parse_helper_output(1, stdout) is None


def test_parse_helper_output_returns_none_for_invalid_json() -> None:
    # covers: Test-6 (예외를 던지지 않고 None으로 부모 크래시를 막는다)
    assert parse_helper_output(0, "not json") is None


def test_parse_helper_output_returns_none_for_empty_stdout() -> None:
    # covers: Test-6
    assert parse_helper_output(0, "") is None


# -- login_and_capture: 부모↔헬퍼 서브프로세스 배선(R3 리뷰 반영) -----------------------


def _helper_result_path(kwargs: dict[str, object]) -> Path:
    """runner에 넘어온 env에서 헬퍼가 써야 할 결과 파일 경로를 꺼낸다."""
    env = kwargs["env"]
    assert isinstance(env, dict), "부모는 헬퍼에 env를 넘겨야 한다(결과 경로 전달 수단)"
    return Path(env[_const(_HELPER_RESULT_ENV)])


def test_login_and_capture_parses_helper_result_file() -> None:
    # covers: Test-10 (R3 추가 — 헬퍼가 결과 파일에 쓴 JSON을 읽어 헤더로 파싱한다)
    def fake_runner(_cmd: list[str], **kwargs: object) -> object:
        # 헬퍼 대역: 환경변수로 지정된 결과 경로에 captured JSON을 쓴다.
        _helper_result_path(kwargs).write_text(
            json.dumps(
                {
                    "status": "captured",
                    "cookies": [
                        {"name": "NID_AUT", "value": "a", "domain": ".naver.com"},
                        {"name": "NID_SES", "value": "b", "domain": ".naver.com"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    assert login_and_capture(runner=fake_runner) == "NID_AUT=a; NID_SES=b"


def test_login_and_capture_returns_none_when_helper_writes_nothing() -> None:
    # covers: Test-10 (헬퍼가 결과 파일을 안 남기면(크래시/취소) None)
    def fake_runner(_cmd: list[str], **_kwargs: object) -> object:
        return SimpleNamespace(returncode=0)  # 결과 파일 미기록

    assert login_and_capture(runner=fake_runner) is None


def test_login_and_capture_returns_none_on_subprocess_timeout() -> None:
    # covers: Test-11 (R3 M1 — 헬퍼가 시간 내 안 끝나면 부모가 타임아웃으로 None)
    def fake_runner(cmd: list[str], **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd, float(kwargs.get("timeout", 0)))  # type: ignore[arg-type]

    assert login_and_capture(runner=fake_runner) is None


# -- 헬퍼 기동 계약: argv -> 환경변수(W-4) ----------------------------------------------
# 주의: 여기부터의 covers 태그는 docs/handoff-velopack-migration.md §5 번호다. 위쪽
# 테스트들의 Test-3~11은 앱 내 웹뷰 로그인 기능의 이전 핸드오프 번호이며 별개 체계다.
#
# flet 러너의 Dart 진입점은 인자가 하나라도 있으면 개발자 모드로 판정해 파이썬을 실행하지
# 않는다. 그래서 헬퍼 모드/결과 경로는 argv가 아니라 환경변수로만 전달해야 하고, 헬퍼
# 진입 판정도 같은 환경변수를 읽어야 한다. 실제 로그인 창이 뜨는지는 Test-43(실기) 몫이다.


def test_helper_command_carries_no_flag_in_bundled_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-29
    # flet build 배포본 신호(FLET_APP_STORAGE_DATA)가 선 상태 — sys.frozen은 PyInstaller
    # 전용이라 flet build에서는 서지 않으므로 판정 근거로 쓰면 안 된다.
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path / "storage"))
    monkeypatch.delenv(_const(_HELPER_ENV), raising=False)

    cmd = cookie_login_mod._helper_command()

    assert cmd, "헬퍼 실행 커맨드가 비어 있으면 안 된다"
    assert all(_LEGACY_HELPER_FLAG not in part for part in cmd), (
        f"argv에 헬퍼 플래그가 남아 있으면 flet 러너가 개발자 모드로 빠진다: {cmd}"
    )
    assert all(_const(_HELPER_MODE) not in part for part in cmd), (
        f"헬퍼 모드는 argv가 아니라 환경변수로만 전달해야 한다: {cmd}"
    )


def test_helper_command_carries_no_flag_in_dev_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: Test-29
    monkeypatch.delenv("FLET_APP_STORAGE_DATA", raising=False)
    monkeypatch.delenv(_const(_HELPER_ENV), raising=False)

    cmd = cookie_login_mod._helper_command()

    assert all(_LEGACY_HELPER_FLAG not in part for part in cmd), (
        f"개발 실행에서도 헬퍼 플래그를 argv로 넘기지 않는다: {cmd}"
    )


def test_helper_env_carries_mode_and_result_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-29
    monkeypatch.setenv("NPC_TEST_SENTINEL", "keep-me")
    result_path = tmp_path / "cookies.json"

    env = cookie_login_mod.helper_env(result_path)

    assert env[_const(_HELPER_ENV)] == _const(_HELPER_MODE)
    assert env[_const(_HELPER_RESULT_ENV)] == str(result_path)
    # 기존 환경을 상속해야 한다(PATH·프록시 등이 사라지면 헬퍼가 뜨지 못한다).
    assert env["NPC_TEST_SENTINEL"] == "keep-me"


def test_is_helper_mode_reads_env_not_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-29
    helper_env_name = _const(_HELPER_ENV)
    monkeypatch.setattr(sys, "argv", ["app", _LEGACY_HELPER_FLAG, "/tmp/cookies.json"])
    monkeypatch.delenv(helper_env_name, raising=False)

    # argv에 옛 플래그가 있어도 헬퍼로 진입하지 않는다(판정 근거는 환경변수뿐).
    assert cookie_login_mod.is_helper_mode() is False

    monkeypatch.setenv(helper_env_name, _const(_HELPER_MODE))
    assert cookie_login_mod.is_helper_mode() is True

    monkeypatch.setenv(helper_env_name, "some-other-mode")
    assert cookie_login_mod.is_helper_mode() is False


def test_login_and_capture_passes_helper_env_to_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: Test-29 (부모가 실제로 환경변수 경로로 헬퍼를 기동한다)
    monkeypatch.delenv(_const(_HELPER_ENV), raising=False)
    seen: list[tuple[list[str], dict[str, object]]] = []

    def fake_runner(cmd: list[str], **kwargs: object) -> object:
        seen.append((cmd, kwargs))
        _helper_result_path(kwargs).write_text(
            json.dumps(
                {
                    "status": "captured",
                    "cookies": [{"name": "NID_AUT", "value": "a", "domain": ".naver.com"}],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    assert login_and_capture(runner=fake_runner) == "NID_AUT=a"

    cmd, kwargs = seen[0]
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env[_const(_HELPER_ENV)] == _const(_HELPER_MODE)
    assert all(_LEGACY_HELPER_FLAG not in part for part in cmd)
    # 결과 경로는 argv가 아니라 환경변수에만 있다.
    assert all(env[_const(_HELPER_RESULT_ENV)] not in part for part in cmd)


# -- 결과 파일의 노출 최소화 --------------------------------------------------
# 주의: 여기부터의 covers 태그는 ``cred/`` 접두사로 docs/handoff-credential-storage.md
# 의 번호임을 밝힌다. 이 파일에는 이미 두 개의 번호 체계가 섞여 있다(위 참고).
#
# 결과 파일에는 세션 쿠키가 평문으로 담긴다. 보관은 OS 자격증명 보관소로 옮겼지만
# 이 취득 순간의 파일은 남아 있으므로, 권한과 수명을 좁히는 것이 유일한 방어다.


def test_result_file_is_removed_as_soon_as_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-6
    # 임시 디렉터리 정리에만 맡기면, 읽은 뒤 부모가 죽었을 때 평문 쿠키가 남는다.
    # Windows %TEMP%는 자동 정리가 사실상 없고 macOS는 재부팅 때만 정리한다.
    class _KeepDir:
        """정리하지 않는 TemporaryDirectory 대역 — 명시적 삭제 여부를 관찰하려면 필요하다."""

        def __enter__(self) -> str:
            return str(tmp_path)

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(cookie_login_mod.tempfile, "TemporaryDirectory", lambda: _KeepDir())
    seen: list[Path] = []

    def fake_runner(_cmd: list[str], **kwargs: object) -> object:
        path = _helper_result_path(kwargs)
        seen.append(path)
        path.write_text(
            json.dumps(
                {
                    "status": "captured",
                    "cookies": [{"name": "NID_AUT", "value": "a", "domain": ".naver.com"}],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    assert login_and_capture(runner=fake_runner) == "NID_AUT=a"

    assert seen, "헬퍼 대역이 결과 경로를 받지 못했다"
    assert not seen[0].exists(), "결과 파일을 읽은 즉시 지워야 한다"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 권한 비트가 없는 플랫폼")
def test_helper_writes_the_result_file_owner_only(tmp_path: Path) -> None:
    # covers: cred/Test-6
    # write_text는 0644에서 umask를 뺀 권한으로 만든다. 세션 쿠키가 담기는 파일이므로
    # 디렉터리 권한에 기대지 않고 파일 자체를 소유자 전용으로 만든다.
    result = tmp_path / "cookies.json"

    cookie_login_mod._write_result(result, "captured", [("NID_AUT", "a", ".naver.com")])

    assert result.stat().st_mode & 0o777 == 0o600


def test_result_write_failure_does_not_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-6
    # 기록 실패가 예외로 새어 나가면 완료 플래그가 서지 않아 로그인 창이 닫히지 않고,
    # 부모가 상한(약 5분 30초)까지 기다린 뒤에야 실패로 처리한다. 무증상 정지가 된다.
    def boom(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("잠김")

    monkeypatch.setattr(cookie_login_mod, "_write_result", boom)

    cookie_login_mod._write_result_safely(tmp_path / "c.json", "captured", [])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 심볼릭 링크 동작")
def test_result_file_refuses_to_follow_a_symlink(tmp_path: Path) -> None:
    # covers: cred/Test-6
    # 공격자가 결과 경로에 링크를 미리 놓아 두면 세션 쿠키가 그 대상에 쓰인다.
    target = tmp_path / "attacker.json"
    link = tmp_path / "cookies.json"
    link.symlink_to(target)

    with pytest.raises(OSError):
        cookie_login_mod._write_result(link, "captured", [("NID_AUT", "a", ".naver.com")])

    assert not target.exists(), "링크 대상에 자격증명이 쓰이면 안 된다"


def test_run_helper_writes_through_the_non_raising_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-6
    # _write_result_safely 자체가 예외를 삼키는지는 위에서 검증했지만, run_helper가 그것을
    # **쓰는지**는 별개다. 배선을 _write_result로 되돌리면 무증상 5분 30초 정지가 그대로
    # 돌아오는데, 그 회귀를 잡는 것은 이 테스트뿐이다.
    used: list[str] = []
    monkeypatch.setattr(
        cookie_login_mod,
        "_write_result_safely",
        lambda path, status, triples: used.append(status),
    )
    monkeypatch.setenv(_const(_HELPER_RESULT_ENV), str(tmp_path / "cookies.json"))

    captured = threading.Event()

    class _Signal:
        """``events.loaded += handler`` 를 받는 최소 대역."""

        def __init__(self) -> None:
            self.handlers: list[object] = []

        def __iadd__(self, handler: object) -> _Signal:
            self.handlers.append(handler)
            return self

    class _Window:
        def __init__(self) -> None:
            self.events = SimpleNamespace(loaded=_Signal())
            self.destroyed = False

        def get_cookies(self) -> list[SimpleCookie]:
            return [_simple_cookie("NID_AUT", "a", ".naver.com")]

        def destroy(self) -> None:
            self.destroyed = True
            captured.set()

    window = _Window()

    def fake_start(**_kwargs: object) -> None:
        # 실제 웹뷰가 로그인 페이지를 다 그렸을 때처럼 loaded 핸들러를 부른다.
        for handler in window.events.loaded.handlers:
            handler()
        assert captured.wait(5.0), "수거 경로가 끝나지 않았다"

    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(create_window=lambda *a, **kw: window, start=fake_start),
    )

    assert cookie_login_mod.run_helper() == 0

    assert used == ["captured"], "run_helper는 예외를 삼키는 래퍼를 거쳐 기록해야 한다"
    assert window.destroyed
