"""카페 세션 쿠키를 파일에서 읽고 내부 저장소에 보관한다.

브라우저 확장으로 내보낸 쿠키 파일(Netscape ``cookies.txt`` 또는 JSON)을 파싱해
네이버 쿠키만 골라 ``"name=value; ..."`` 헤더 문자열로 만든다. GUI의 "쿠키 업데이트"
버튼이 이 문자열을 앱 내부 저장소에 저장하고, CLI/GUI가 카페 접근에 재사용한다.

.. note::
    저장되는 쿠키는 로그인 세션 그 자체다. 앱 내부 저장소(사용자 전용 경로)에
    평문으로 두되, 가능한 플랫폼에서는 소유자 전용 권한(0o600)으로 제한한다.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from .errors import InvalidCookieFile

logger = logging.getLogger(__name__)

# 내부 저장소 하위 디렉터리·파일 이름.
_APP_DIR = "naver-blog-crawler"
_COOKIE_FILE = "cafe_cookie.txt"

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
    naver = _select_naver(cookies)
    if not naver:
        raise InvalidCookieFile(
            "쿠키 파일에서 naver.com 쿠키를 찾지 못했습니다. "
            "네이버에 로그인한 상태에서 내보냈는지 확인하세요."
        )
    if not any(name == "NID_AUT" for name, _ in naver):
        logger.warning(
            "쿠키 파일에 NID_AUT가 없습니다. 로그인 세션이 아닐 수 있어 "
            "등급 제한 게시판 접근이 실패할 수 있습니다."
        )
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

    패키징된 앱은 flet이 주는 포터블 저장 경로(``FLET_APP_STORAGE_DATA``)를, 개발
    실행에서는 플랫폼별 사용자 데이터 경로를 쓴다.
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


def stored_cookie_path(directory: Path | None = None) -> Path:
    """저장된 쿠키 파일 경로(기본: 앱 내부 저장소)."""
    return (directory or app_data_dir()) / _COOKIE_FILE


def save_cookie(cookie: str, directory: Path | None = None) -> Path:
    """쿠키 문자열을 내부 저장소에 저장하고 경로를 돌려준다(소유자 전용 권한 시도)."""
    path = stored_cookie_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cookie.strip(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # 일부 플랫폼(Windows 등)은 POSIX 권한을 지원하지 않는다. 저장 자체는 성공했다.
        logger.debug("쿠키 파일 권한 설정을 건너뜀(플랫폼 미지원일 수 있음)", exc_info=True)
    return path


def load_cookie(directory: Path | None = None) -> str | None:
    """저장된 쿠키 문자열을 읽는다(없으면 None)."""
    path = stored_cookie_path(directory)
    if not path.exists():
        return None
    cookie = path.read_text(encoding="utf-8").strip()
    return cookie or None
