"""쿠키 파일 파싱(Netscape/JSON)과 내부 저장/로드 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from naver_post_crawler.cookie import (
    app_data_dir,
    format_cookie_header,
    load_cookie,
    parse_cookie_file,
    save_cookie,
    stored_cookie_path,
)
from naver_post_crawler.errors import InvalidCookieFile

# "Get cookies.txt LOCALLY" 확장이 내보내는 Netscape 형식(도메인 평문, 3줄 헤더).
_NETSCAPE = """# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
# This is a generated file! Do not edit.

.naver.com\tTRUE\t/\tTRUE\t1783440000\tNID_AUT\tAUTVALUE
.naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tSESVALUE
.google.com\tTRUE\t/\tFALSE\t0\tOTHER\tSHOULD_NOT_APPEAR
"""

# curl/yt-dlp 계열이 쓰는 #HttpOnly_ 접두사 변형(NID_AUT은 HttpOnly).
_NETSCAPE_HTTPONLY = (
    "#HttpOnly_.naver.com\tTRUE\t/\tTRUE\t1783440000\tNID_AUT\tAUTVALUE\n"
    ".naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tSESVALUE\n"
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_netscape_extracts_naver_and_filters_others(tmp_path: Path) -> None:
    path = _write(tmp_path, "cookies.txt", _NETSCAPE)
    assert parse_cookie_file(path) == "NID_AUT=AUTVALUE; NID_SES=SESVALUE"


def test_parse_netscape_handles_httponly_prefix(tmp_path: Path) -> None:
    path = _write(tmp_path, "cookies.txt", _NETSCAPE_HTTPONLY)
    assert parse_cookie_file(path) == "NID_AUT=AUTVALUE; NID_SES=SESVALUE"


def test_parse_json_list_of_cookie_objects(tmp_path: Path) -> None:
    data = [
        {"domain": ".naver.com", "name": "NID_AUT", "value": "AUTVALUE", "httpOnly": True},
        {"domain": ".naver.com", "name": "NID_SES", "value": "SESVALUE"},
        {"domain": ".google.com", "name": "OTHER", "value": "X"},
    ]
    path = _write(tmp_path, "cookies.json", json.dumps(data))
    assert parse_cookie_file(path) == "NID_AUT=AUTVALUE; NID_SES=SESVALUE"


def test_parse_json_wrapped_in_object(tmp_path: Path) -> None:
    data = {"cookies": [{"domain": "cafe.naver.com", "name": "NID_SES", "value": "S"}]}
    path = _write(tmp_path, "c.json", json.dumps(data))
    assert parse_cookie_file(path) == "NID_SES=S"


def test_parse_dedupes_by_name_keeping_first(tmp_path: Path) -> None:
    text = (
        ".naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tFIRST\n"
        "cafe.naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tSECOND\n"
    )
    path = _write(tmp_path, "cookies.txt", text)
    assert parse_cookie_file(path) == "NID_SES=FIRST"


def test_parse_bom_prefixed_json(tmp_path: Path) -> None:
    # Windows 편집기가 붙이는 UTF-8 BOM이 앞에 있어도 JSON으로 올바로 인식해야 한다.
    data = [{"domain": ".naver.com", "name": "NID_SES", "value": "S"}]
    path = tmp_path / "bom.json"
    path.write_text("﻿" + json.dumps(data), encoding="utf-8")
    assert parse_cookie_file(path) == "NID_SES=S"


def test_parse_preserves_special_characters_in_value(tmp_path: Path) -> None:
    # 네이버 쿠키 값에는 =, +, / 가 들어간다. 값을 이 문자들로 쪼개면 안 된다.
    value = "AAAB+c/d==ef=gh"
    path = _write(tmp_path, "cookies.txt", f".naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\t{value}\n")
    assert parse_cookie_file(path) == f"NID_SES={value}"


def test_parse_json_singular_cookie_key(tmp_path: Path) -> None:
    data = {"cookie": [{"domain": ".naver.com", "name": "NID_SES", "value": "S"}]}
    path = _write(tmp_path, "c.json", json.dumps(data))
    assert parse_cookie_file(path) == "NID_SES=S"


def test_parse_netscape_skips_short_lines(tmp_path: Path) -> None:
    text = (
        "too\tfew\tfields\n"  # 7필드 미만 → 무시
        ".naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tS\n"
    )
    path = _write(tmp_path, "cookies.txt", text)
    assert parse_cookie_file(path) == "NID_SES=S"


def test_parse_filters_lookalike_domains(tmp_path: Path) -> None:
    # notnaver.com / naver.com.evil.io 같은 유사 도메인은 naver로 오인하면 안 된다.
    text = (
        "notnaver.com\tTRUE\t/\tFALSE\t0\tFAKE\tX\n"
        "naver.com.evil.io\tTRUE\t/\tFALSE\t0\tFAKE2\tY\n"
        ".naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tS\n"
    )
    path = _write(tmp_path, "cookies.txt", text)
    assert parse_cookie_file(path) == "NID_SES=S"


def test_parse_warns_when_nid_aut_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _write(tmp_path, "cookies.txt", ".naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tS\n")
    with caplog.at_level("WARNING", logger="naver_post_crawler.cookie"):
        result = parse_cookie_file(path)
    assert result == "NID_SES=S"
    assert "NID_AUT" in caplog.text


def test_parse_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidCookieFile):
        parse_cookie_file(tmp_path / "does_not_exist.txt")


def test_parse_empty_file_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.txt", "   \n")
    with pytest.raises(InvalidCookieFile):
        parse_cookie_file(path)


def test_parse_no_naver_cookies_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "cookies.txt", ".google.com\tTRUE\t/\tFALSE\t0\tX\tY\n")
    with pytest.raises(InvalidCookieFile):
        parse_cookie_file(path)


def test_parse_invalid_json_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.json", "[{not valid json")
    with pytest.raises(InvalidCookieFile):
        parse_cookie_file(path)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    assert load_cookie(directory=tmp_path) is None
    saved = save_cookie("NID_AUT=a; NID_SES=b", directory=tmp_path)
    assert saved == stored_cookie_path(tmp_path)
    assert saved.exists()
    assert load_cookie(directory=tmp_path) == "NID_AUT=a; NID_SES=b"


def test_save_strips_whitespace(tmp_path: Path) -> None:
    save_cookie("  NID_SES=x  \n", directory=tmp_path)
    assert load_cookie(directory=tmp_path) == "NID_SES=x"


def test_load_absent_returns_none(tmp_path: Path) -> None:
    assert load_cookie(directory=tmp_path) is None


def test_app_data_dir_uses_flet_storage_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "flet-storage"
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(target))
    assert app_data_dir() == target
    assert target.exists()  # 없으면 만든다


# -- 웹뷰 로그인 헬퍼와 공유하는 포맷/필터 계층(format_cookie_header) --------------------
# 파일 경로(parse_cookie_file)와 웹뷰 경로(cookie_login.py)가 naver 필터 + 조인 로직을
# 공유하도록 뽑아낸 SSoT. _select_naver의 필터·중복 제거 규칙을 그대로 보존해야 한다.


def test_format_cookie_header_filters_dedups_and_joins() -> None:
    # covers: Test-1
    triples = [
        ("NID_AUT", "AUTVALUE", ".naver.com"),
        ("NID_SES", "SESVALUE", "cafe.naver.com"),  # 하위 도메인도 포함
        ("NID_SES", "STALE", ".naver.com"),  # 같은 이름 중복 → 먼저 나온 값 유지
        ("OTHER", "X", ".google.com"),  # 비-naver 도메인은 제외
    ]
    assert format_cookie_header(triples) == "NID_AUT=AUTVALUE; NID_SES=SESVALUE"


def test_format_cookie_header_returns_empty_string_when_no_naver_cookies() -> None:
    # covers: Test-2
    triples = [("OTHER", "X", ".google.com")]
    assert format_cookie_header(triples) == ""


def test_parse_cookie_file_still_raises_when_no_naver_cookies(tmp_path: Path) -> None:
    # covers: Test-2 (회귀 가드: format_cookie_header 추출 후에도 파일 경로는 여전히
    # naver 쿠키가 하나도 없으면 InvalidCookieFile을 던져야 한다)
    path = _write(tmp_path, "cookies.txt", ".google.com\tTRUE\t/\tFALSE\t0\tX\tY\n")
    with pytest.raises(InvalidCookieFile):
        parse_cookie_file(path)
