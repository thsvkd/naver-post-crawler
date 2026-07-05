"""웹뷰 헬퍼 쿠키 정규화 및 부모↔헬퍼 계약 파서 테스트.

pywebview 6.2.1 스파이크 실증(핸드오프 §4)에 따르면 ``get_cookies()``는
``list[http.cookies.SimpleCookie]``를 돌려주고, 헬퍼 서브프로세스는 결과를 stdout에
JSON 한 줄로 반환한다(핸드오프 §2). 이 두 계약을 검증한다.
"""

from __future__ import annotations

import json
import subprocess
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

from naver_post_crawler.cookie import format_cookie_header
from naver_post_crawler.cookie_login import (
    login_and_capture,
    normalize_cookies,
    parse_helper_output,
)


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


def test_login_and_capture_parses_helper_result_file() -> None:
    # covers: Test-10 (R3 추가 — 헬퍼가 결과 파일에 쓴 JSON을 읽어 헤더로 파싱한다)
    def fake_runner(cmd: list[str], **_kwargs: object) -> object:
        # 헬퍼 대역: 커맨드 끝의 결과 경로에 captured JSON을 쓴다.
        Path(cmd[-1]).write_text(
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
