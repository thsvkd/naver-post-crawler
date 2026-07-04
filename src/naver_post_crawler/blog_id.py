"""입력값(블로그 아이디 또는 URL)에서 블로그 아이디를 추출한다.

다음 형태를 모두 인식한다.
- 순수 아이디: ``winter9377``
- 블로그 URL: ``https://m.blog.naver.com/winter9377``, ``blog.naver.com/winter9377``
- 포스트 URL: ``https://blog.naver.com/winter9377/223189475037``
- PostView URL: ``https://m.blog.naver.com/PostView.naver?blogId=winter9377&logNo=...``
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .errors import InvalidBlogReference

# 네이버 블로그 아이디로 허용되는 문자(영숫자·하이픈·언더스코어).
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


def resolve_blog_id(value: str) -> str:
    """블로그 아이디 또는 URL에서 블로그 아이디를 추출한다.

    Raises:
        InvalidBlogReference: 입력에서 유효한 블로그 아이디를 찾지 못한 경우.
    """
    value = value.strip()
    if not value:
        raise InvalidBlogReference("블로그 아이디 또는 URL이 비어 있습니다.")

    if value.startswith(("http://", "https://")) or "naver.com" in value:
        candidate = _extract_from_url(value)
    else:
        candidate = value

    if not _ID_RE.match(candidate):
        raise InvalidBlogReference(f"블로그 아이디를 인식할 수 없습니다: {value!r}")
    return candidate


def _extract_from_url(value: str) -> str:
    """URL에서 블로그 아이디 후보를 뽑는다."""
    # urlparse가 호스트·경로를 제대로 나누도록 스킴을 보강한다.
    raw = value if "://" in value else f"https://{value}"
    parsed = urlparse(raw)

    # PostView.naver?blogId=... 형태는 쿼리에서 직접 얻는다.
    query = parse_qs(parsed.query)
    if query.get("blogId"):
        return query["blogId"][0]

    # 그 외에는 경로의 첫 세그먼트가 블로그 아이디다.
    segments = [seg for seg in parsed.path.split("/") if seg]
    first = segments[0] if segments else ""
    if not first or first.endswith(".naver"):
        raise InvalidBlogReference(f"URL에서 블로그 아이디를 찾을 수 없습니다: {value!r}")
    return first
