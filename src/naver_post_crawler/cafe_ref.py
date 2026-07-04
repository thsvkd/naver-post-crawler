"""입력값(카페 URL)에서 카페 참조 정보를 추출한다.

카페는 블로그와 달리 하나의 아이디로 끝나지 않고 클럽 URL(예: ``steamindiegame``)
또는 숫자 clubId(=cafeId), 게시판(menuId), 글(articleId)이 여러 조합으로 들어온다.
아래 형태를 인식한다.

- 클럽 URL: ``https://cafe.naver.com/steamindiegame``
- 클럽 URL + 글: ``https://cafe.naver.com/steamindiegame/12345``
- 신형 SPA 게시판: ``https://cafe.naver.com/ca-fe/cafes/29434212/menus/5``
- 신형 SPA 글: ``https://cafe.naver.com/f-e/cafes/29434212/articles/12345?menuid=5``
- 구형 링크: ``https://cafe.naver.com/ArticleList.nhn?search.clubid=29434212&search.menuid=5``
- 모바일: ``https://m.cafe.naver.com/ca/steamindiegame``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .errors import InvalidCafeReference

# 카페 참조로 인식하는 호스트 표식.
_CAFE_HOST = "cafe.naver.com"

# 클럽 URL(vanity name)이 아닌, 경로의 구조적 세그먼트.
# 신형 SPA 접두(f-e/ca-fe), 모바일 접두(ca), 경로 구조 키워드(cafes/menus/articles)만
# 예약한다. 이 목록은 실제 URL 구조에서 나온 것으로 한정하고, 일반 단어를 넣어
# 정상 클럽 URL을 거부하지 않는다.
_RESERVED_SEGMENTS = frozenset({"f-e", "ca-fe", "ca", "cafes", "menus", "articles"})

_CAFES_ID_RE = re.compile(r"/cafes/(\d+)")
_MENUS_ID_RE = re.compile(r"/menus/(\d+)")
_ARTICLES_ID_RE = re.compile(r"/articles/(\d+)")


@dataclass(frozen=True, slots=True)
class CafeReference:
    """입력에서 뽑아낸 카페 참조 정보.

    ``cafe_id``(숫자 clubId)를 이미 알면 곧장 API 호출에 쓰고, 없으면 ``club_url``로
    카페 홈에서 clubId를 해석한다. ``menu_id``가 있으면 그 게시판만, 없으면 접근
    가능한 모든 게시판을 대상으로 한다. ``article_id``는 단일 글 참조일 때 채워진다.
    """

    club_url: str | None = None
    cafe_id: int | None = None
    menu_id: int | None = None
    article_id: int | None = None


def is_cafe_reference(value: str) -> bool:
    """입력이 네이버 카페 주소인지(블로그가 아닌지) 판별한다."""
    return _CAFE_HOST in value.lower()


def resolve_cafe_reference(value: str) -> CafeReference:
    """카페 URL에서 :class:`CafeReference`를 추출한다.

    Raises:
        InvalidCafeReference: 카페 주소가 아니거나 clubId/클럽 URL을 찾지 못한 경우.
    """
    value = value.strip()
    if not value:
        raise InvalidCafeReference("카페 주소가 비어 있습니다.")

    raw = value if "://" in value else f"https://{value}"
    parsed = urlparse(raw)
    if _CAFE_HOST not in parsed.netloc.lower():
        raise InvalidCafeReference(f"네이버 카페 주소가 아닙니다: {value!r}")

    path = parsed.path
    query = parse_qs(parsed.query)

    cafe_id = _first_int(_CAFES_ID_RE.search(path)) or _query_int(query, "clubid", "clubId")
    menu_id = _first_int(_MENUS_ID_RE.search(path)) or _query_int(query, "menuid", "menuId")
    article_id = _first_int(_ARTICLES_ID_RE.search(path)) or _query_int(
        query, "articleid", "articleId"
    )

    club_url, article_from_path = _extract_club_url(path)
    if article_id is None:
        article_id = article_from_path

    if cafe_id is None and club_url is None:
        raise InvalidCafeReference(f"카페 clubId나 클럽 URL을 찾을 수 없습니다: {value!r}")

    return CafeReference(club_url=club_url, cafe_id=cafe_id, menu_id=menu_id, article_id=article_id)


def _extract_club_url(path: str) -> tuple[str | None, int | None]:
    """경로에서 클럽 URL(vanity name)과, 바로 뒤 숫자 세그먼트(글 번호)를 뽑는다."""
    segments = [seg for seg in path.split("/") if seg]
    for index, seg in enumerate(segments):
        low = seg.lower()
        if low in _RESERVED_SEGMENTS or seg.isdigit() or low.endswith((".nhn", ".naver")):
            continue
        # 클럽 URL 바로 뒤가 숫자면 글 번호다(예: /steamindiegame/12345).
        following = segments[index + 1] if index + 1 < len(segments) else ""
        article_id = int(following) if following.isdigit() else None
        return seg, article_id
    return None, None


def _first_int(match: re.Match[str] | None) -> int | None:
    return int(match.group(1)) if match is not None else None


def _query_int(query: dict[str, list[str]], *names: str) -> int | None:
    """쿼리에서 이름이 맞는 첫 정수 값을 돌려준다.

    구형 카페 링크는 ``search.clubid``처럼 점 표기 키를 쓰므로, 키의 마지막 점
    세그먼트가 요청한 이름과 같으면 매칭한다(대소문자 무시).
    """
    wanted = {name.lower() for name in names}
    for key, values in query.items():
        tail = key.rsplit(".", 1)[-1].lower()
        if tail in wanted and values and values[0].isdigit():
            return int(values[0])
    return None
