"""카페 세션 쿠키를 파일에서 읽고 내부 저장소에 보관한다.

브라우저 확장으로 내보낸 쿠키 파일(Netscape ``cookies.txt`` 또는 JSON)을 파싱해
네이버 쿠키만 골라 ``"name=value; ..."`` 헤더 문자열로 만든다. GUI의 "쿠키 업데이트"
버튼이 이 문자열을 앱 내부 저장소에 저장하고, CLI/GUI가 카페 접근에 재사용한다.

.. note::
    저장되는 쿠키는 로그인 세션 그 자체다. 그래서 파일이 아니라 **OS 자격증명 보관소**에
    보관한다(:mod:`naver_post_crawler.credentials`). v0.1.1까지 쓰던 평문 파일은
    :func:`migrate_legacy_cookie`이 앱 시작 시 보관소로 옮기고 삭제한다.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from . import credentials
from .errors import CredentialStoreError, InvalidCookieFile

logger = logging.getLogger(__name__)

# 내부 저장소 하위 디렉터리·파일 이름.
_APP_DIR = "naver-post-crawler"
_COOKIE_FILE = "cafe_cookie.txt"
# v0.1.1까지 세션 쿠키를 평문으로 담던 파일. 이제는 이관 후 삭제 대상일 뿐이다.
LEGACY_COOKIE_FILE = _COOKIE_FILE

# Netscape 포맷에서 HttpOnly 쿠키(NID_AUT 등)는 이 접두사로 위장돼 주석처럼 보인다.
_HTTPONLY_PREFIX = "#HttpOnly_"


def parse_cookie_file(path: str | Path) -> str:
    """쿠키 파일에서 네이버 쿠키를 골라 ``"name=value; ..."`` 헤더 문자열로 만든다.

    Netscape ``cookies.txt``와 JSON(Cookie-Editor/EditThisCookie 계열)을 모두 인식한다.

    Raises:
        InvalidCookieFile: 파일이 없거나 비었거나, 형식을 알 수 없거나, naver.com
            쿠키가 하나도 없는 경우.
    """
    file = Path(path)
    try:
        # utf-8-sig: Windows 편집기 등이 붙이는 BOM(U+FEFF)을 자동으로 제거한다.
        # strip()은 BOM을 공백으로 보지 않아, BOM이 남으면 JSON/Netscape 판별이 어긋난다.
        text = file.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError as exc:
        raise InvalidCookieFile(f"쿠키 파일을 열 수 없습니다: {file} ({exc})") from exc
    if not text:
        raise InvalidCookieFile(f"쿠키 파일이 비어 있습니다: {file}")

    cookies = _parse_json(text) if text[0] in "[{" else _parse_netscape(text)
    header = format_cookie_header(cookies)
    if not header:
        raise InvalidCookieFile(
            "쿠키 파일에서 naver.com 쿠키를 찾지 못했습니다. "
            "네이버에 로그인한 상태에서 내보냈는지 확인하세요."
        )
    if not any(name == "NID_AUT" for name, _, domain in cookies if _is_naver_domain(domain)):
        logger.warning(
            "쿠키 파일에 NID_AUT가 없습니다. 로그인 세션이 아닐 수 있어 "
            "등급 제한 게시판 접근이 실패할 수 있습니다."
        )
    return header


def format_cookie_header(cookies: list[tuple[str, str, str]]) -> str:
    """``(name, value, domain)`` 목록에서 naver.com 쿠키만 골라 헤더 문자열로 만든다.

    naver.com(하위 도메인 포함) 쿠키만 이름 기준 중복 없이(먼저 나온 값 유지) 뽑아
    ``"name=value; name=value"``로 조인한다. naver 쿠키가 하나도 없으면 빈 문자열.

    쿠키 파일 경로(:func:`parse_cookie_file`)와 웹뷰 로그인 경로
    (:mod:`naver_post_crawler.cookie_login`)가 이 필터·조인 규칙을 공유하는 SSoT다.
    """
    naver = _select_naver(cookies)
    return "; ".join(f"{name}={value}" for name, value in naver)


def _parse_netscape(text: str) -> list[tuple[str, str, str]]:
    """Netscape cookie file 텍스트를 (name, value, domain) 목록으로 파싱한다.

    각 줄은 탭으로 나뉜 7개 필드다: domain, includeSubdomains, path, secure, expiry,
    name, value. ``#HttpOnly_`` 접두사가 붙은 줄은 HttpOnly 쿠키이므로 접두사를 떼고
    처리하고, 그 밖의 ``#`` 주석과 빈 줄은 건너뛴다.
    """
    out: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            if raw.startswith(_HTTPONLY_PREFIX):
                raw = raw[len(_HTTPONLY_PREFIX) :]
            else:
                continue
        fields = raw.split("\t")
        if len(fields) < 7:
            continue
        out.append((fields[5], fields[6], fields[0]))
    return out


def _parse_json(text: str) -> list[tuple[str, str, str]]:
    """JSON 쿠키 내보내기(쿠키 객체 리스트)를 (name, value, domain) 목록으로 파싱한다."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidCookieFile(f"쿠키 파일의 JSON을 해석할 수 없습니다: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("cookies") or data.get("cookie") or []
    if not isinstance(data, list):
        raise InvalidCookieFile("JSON 쿠키 형식을 인식할 수 없습니다(쿠키 객체 배열이 아님).")
    out: list[tuple[str, str, str]] = []
    for item in data:
        if isinstance(item, dict) and "name" in item and "value" in item:
            out.append((str(item["name"]), str(item["value"]), str(item.get("domain", ""))))
    return out


def _is_naver_domain(domain: str) -> bool:
    """도메인이 naver.com 또는 그 하위 도메인인지 정확히 판정한다.

    부분 문자열 매칭(예: ``notnaver.com``, ``naver.com.evil.io``)을 걸러내려고
    접미사로 판정한다. 쿠키 도메인은 ``.naver.com``처럼 앞에 점이 붙을 수 있다.
    """
    d = domain.strip().lower().lstrip(".")
    return d == "naver.com" or d.endswith(".naver.com")


def _select_naver(cookies: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """naver.com 도메인 쿠키만 골라 (name, value)로, 이름 기준 중복 없이 돌려준다."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for name, value, domain in cookies:
        if not _is_naver_domain(domain) or name in seen:
            continue
        seen.add(name)
        out.append((name, value))
    return out


def app_data_dir() -> Path:
    """앱 내부 저장소 디렉터리(없으면 만든다).

    flet 러너가 프로덕션 실행에서 세워 주는 저장 경로(``FLET_APP_STORAGE_DATA``)가 있으면
    그것을 쓰고, 없으면 플랫폼별 사용자 데이터 경로를 쓴다.

    .. warning::
        **실행 파일 옆에 저장하지 않는다.** Velopack 설치본은 Windows에서
        ``%LocalAppData%\\<PackId>\\current\\``를, macOS에서 ``.app`` 번들을 업데이트할 때
        통째로 교체한다. 그 안에 데이터를 두면 업데이트 한 번에 쿠키가 사라진다.
        (``sys.frozen``은 PyInstaller가 세우는 값이라 ``flet build`` 산출물에서는 서지도
        않는다 — 판정 근거로 쓸 수 없다.)
    """
    flet_storage = os.environ.get("FLET_APP_STORAGE_DATA")
    if flet_storage:
        base = Path(flet_storage)
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(root) / _APP_DIR
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / _APP_DIR
    else:
        root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(root) / _APP_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def legacy_cookie_path(directory: Path | None = None) -> Path:
    """평문 쿠키 파일 경로. 이제는 **이관 후 삭제 대상**일 뿐 저장에 쓰지 않는다."""
    return (directory or app_data_dir()) / LEGACY_COOKIE_FILE


def save_cookie(cookie: str) -> None:
    """쿠키 문자열을 OS 보관소에 저장한다(파일로 쓰지 않는다).

    Raises:
        CredentialStoreError: 보관소에 기록하지 못했을 때. 호출자가 사용자에게 알린다.
    """
    credentials.save(cookie.strip())


def load_cookie() -> str | None:
    """보관된 쿠키 문자열(없으면 ``None``). 보관소를 못 읽어도 예외를 올리지 않는다."""
    return credentials.load()


def delete_cookie() -> None:
    """보관된 쿠키를 지운다(없어도 조용히 넘어간다)."""
    credentials.delete()


def migrate_legacy_cookie(directory: Path | None = None) -> None:
    """평문 쿠키 파일이 남아 있으면 보관소로 옮기고 **파일을 지운다**.

    앱이 시작할 때 부른다. 설치 시점이 아니라 시작 시점인 이유는 OTA 업데이트가 새 설치가
    아니어서, 설치 훅에만 걸면 기존 사용자 대부분이 누락되기 때문이다.

    보관소 기록이 실패해도 평문 파일은 지운다. 자격증명 노출을 없애는 것이 이 작업의
    목적이고, 세션 쿠키는 재로그인으로 다시 얻을 수 있는 값이다(핸드오프 D-3).
    어떤 실패도 밖으로 내보내지 않는다 — 이관 때문에 앱이 시작하지 못하면 안 된다.
    """
    path = legacy_cookie_path(directory)
    if not path.exists():
        return
    try:
        cookie = path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.debug("평문 쿠키 파일을 읽지 못했습니다", exc_info=True)
        cookie = ""
    # 이미 보관소에 값이 있으면 덮어쓰지 않는다 — 새 로그인으로 얻은 최신 쿠키를
    # 옛 파일이 되돌리면 안 된다.
    if cookie and credentials.load() is None:
        try:
            credentials.save(cookie)
        except CredentialStoreError:
            logger.warning("평문 쿠키를 보관소로 옮기지 못했습니다. 다시 로그인해 주세요.")
    try:
        path.unlink()
    except OSError:
        logger.warning("평문 쿠키 파일을 지우지 못했습니다: %s", path, exc_info=True)
    else:
        logger.info("평문 쿠키 파일을 OS 보관소로 옮기고 삭제했습니다.")
