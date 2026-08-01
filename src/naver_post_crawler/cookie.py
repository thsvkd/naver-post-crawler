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
from dataclasses import dataclass
from enum import StrEnum
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


class CookieMigration(StrEnum):
    """:func:`migrate_legacy_cookie`의 결과.

    호출자가 사용자에게 알릴 수 있어야 한다. 이관은 로깅이 설정되기 전에 실행되므로
    (앱 시작 직후), 실패를 로그에만 남기면 사용자도 지원자도 흔적을 볼 수 없다.
    """

    NOTHING = "nothing"
    """옮길 평문 파일이 없었다(대부분의 실행)."""

    MOVED = "moved"
    """평문 쿠키를 보관소로 옮기고 파일을 지웠다."""

    KEPT = "kept"
    """보관소에 이미 값이 있어 덮어쓰지 않고 평문 파일만 지웠다."""

    LOST = "lost"
    """보관소에 넣지 못했다 — 사용자는 다시 로그인해야 한다."""


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """이관 결과. **노출과 손실은 독립적인 두 사실이라 한 값으로 접지 않는다.**

    둘을 하나의 열거형으로 합치면 반드시 한쪽이 사라진다. 실제로 "보관소 저장도 실패하고
    파일도 못 지운" 조합에서 노출만 알리면, 사용자는 로그아웃된 줄 모른 채 안내대로 파일을
    지워 **남아 있던 유일한 쿠키 사본을 없앤다** — 그대로 뒀다면 다음 실행에서 이관될 수도
    있었다. 안내가 능동적으로 해로워지는 지점이다.
    """

    outcome: CookieMigration
    """보관소 쪽에서 일어난 일."""

    exposed: bool
    """평문 파일이 **아직 디스크에 남아 있다**(지우지 못했다)."""

    path: Path
    """평문 파일 경로. 안내에 그대로 싣는다."""


def migration_advice(result: MigrationResult) -> str | None:
    """사용자에게 보여야 할 안내 문구(보여줄 것이 없으면 ``None``).

    GUI와 CLI가 같은 판단을 하도록 한곳에 둔다. 순서가 중요하다 — 노출과 손실이 겹치면
    **지우기 전에 재로그인부터** 하라고 해야 한다.
    """
    if result.exposed and result.outcome is CookieMigration.LOST:
        return (
            "이전 버전의 쿠키 파일이 남아 있고 로그인도 풀렸습니다. "
            f"'네이버 로그인'을 다시 한 뒤에 이 파일을 지워 주세요: {result.path}"
        )
    if result.exposed:
        return f"이전 버전의 쿠키 파일을 지우지 못했습니다 — 직접 삭제해 주세요: {result.path}"
    if result.outcome is CookieMigration.LOST:
        return "이전 버전에 저장된 쿠키를 옮기지 못했습니다 — '네이버 로그인'을 다시 해 주세요."
    return None


def migrate_legacy_cookie(directory: Path | None = None) -> MigrationResult:
    """평문 쿠키 파일이 남아 있으면 보관소로 옮기고 **파일을 지운다**.

    앱이 시작할 때 부른다. 설치 시점이 아니라 시작 시점인 이유는 OTA 업데이트가 새 설치가
    아니어서, 설치 훅에만 걸면 기존 사용자 대부분이 누락되기 때문이다.

    보관소 기록이 실패해도 평문 파일은 지운다. 자격증명 노출을 없애는 것이 이 작업의
    목적이고, 세션 쿠키는 재로그인으로 다시 얻을 수 있는 값이다(핸드오프 D-3).
    어떤 실패도 밖으로 내보내지 않는다 — 이관 때문에 앱이 시작하지 못하면 안 된다.
    대신 결과를 :class:`MigrationResult`로 돌려 호출자가 사용자에게 알리게 한다.
    """
    path = legacy_cookie_path(directory)
    if not path.exists():
        return MigrationResult(CookieMigration.NOTHING, exposed=False, path=path)
    try:
        cookie = path.read_text(encoding="utf-8").strip()
    except OSError:
        # 읽지 못한 파일도 지운다(D-3). 안에 세션이 들어 있었을 수 있으므로 손실로 본다.
        logger.warning("평문 쿠키 파일을 읽지 못했습니다", exc_info=True)
        cookie = ""
        outcome = CookieMigration.LOST
    else:
        # 빈 파일은 잃을 것이 없다. 여기서 NOTHING으로 두지 않으면 옛 빈 파일 하나 때문에
        # 사용자에게 "다시 로그인하라"는 잘못된 안내가 나간다.
        outcome = CookieMigration.NOTHING if not cookie else CookieMigration.LOST

    if cookie:
        try:
            # 이미 보관소에 값이 있으면 덮어쓰지 않는다 — 새 로그인으로 얻은 최신 쿠키를
            # 옛 파일이 되돌리면 안 된다. 읽기 실패를 "값 없음"으로 오해하면 바로 그 일이
            # 벌어지므로, 삼키는 load()가 아니라 예외를 올리는 load_strict()를 쓴다.
            if credentials.load_strict() is not None:
                outcome = CookieMigration.KEPT
            else:
                credentials.save(cookie)
                outcome = CookieMigration.MOVED
        except CredentialStoreError:
            # 읽기가 실패한 경우에도 저장을 시도하지 않고 LOST로 끝낸다. 보관소가 비어
            # 있었다면 살릴 수 있었을 쿠키를 잃지만, 읽지 못하는 보관소에 덮어썼다가
            # 최신 값을 지우는 쪽이 더 나쁘다. 사용자에게는 LOST로 고지된다.
            logger.warning("평문 쿠키를 보관소로 옮기지 못했습니다", exc_info=True)

    try:
        path.unlink()
    except OSError:
        # 평문이 그대로 남았다. 백신·인덱서·다른 인스턴스가 파일을 잡고 있으면 실제로
        # 일어난다. 로그로만 남기면 이 시점에는 핸들러가 없어(창 모드는 stderr도 없다)
        # 어디에도 보이지 않으므로, 결과에 실어 올려 호출자가 사용자에게 알리게 한다.
        # **outcome은 그대로 둔다** — 노출은 손실을 덮는 사실이 아니라 별개의 사실이다.
        logger.warning("평문 쿠키 파일을 지우지 못했습니다: %s", path, exc_info=True)
        return MigrationResult(outcome, exposed=True, path=path)
    logger.info("평문 쿠키 파일을 정리했습니다(%s).", outcome.value)
    return MigrationResult(outcome, exposed=False, path=path)
