"""네이버 카페 내부 API HTTP 클라이언트.

카페 웹은 SPA라, 실제 데이터는 공식 오픈 API가 아니라 ``apis.naver.com``
게이트웨이의 내부 JSON API로 로드된다(공식 오픈 API에는 글 "읽기"가 없다).
이 클라이언트는 다음을 사용한다.

- 게시판 목록: ``cafe-web/cafe2/SideMenuList``
- 게시글 목록: ``cafe-web/cafe-boardlist-api/v1/cafes/{cafeId}/menus/{menuId}/articles``
- 게시글 본문: ``cafe-web/cafe-articleapi/v3/cafes/{cafeId}/articles/{articleId}``

.. warning::
    위 엔드포인트는 **비공식·비문서화**라 경로·파라미터·응답 구조가 예고 없이
    바뀔 수 있다. 응답 봉투는 여러 형태를 방어적으로 벗겨 처리하되, 라이브
    검증이 필요하다. 로그인/권한이 필요한 게시판은 유효한 세션 쿠키
    (``NID_AUT``/``NID_SES``)를 ``cookie``로 주입해야 본문을 받을 수 있다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime

import httpx

from .cafe_ref import CafeReference
from .errors import CafeNotFound, LoginRequired, ParseError
from .http import get_with_retry
from .models import KST, PostMeta

logger = logging.getLogger(__name__)

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
)

# 내부 API 엔드포인트(절대 URL). base_url 없이 절대 URL로 호출한다.
_CAFE_HOME_URL = "https://cafe.naver.com/{club}"
_SPA_ARTICLE_URL = "https://cafe.naver.com/f-e/cafes/{cafe_id}/articles/{article_id}"
_ARTICLE_LIST_URL = (
    "https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafe_id}/menus/{menu_id}/articles"
)
_ARTICLE_URL = (
    "https://apis.naver.com/cafe-web/cafe-articleapi/v3/cafes/{cafe_id}/articles/{article_id}"
)

# menuId 0 = "전체글보기". 게시판 목록을 따로 조회하지 않아도 카페 전체 글을
# 최신순으로 준다. 각 글 항목에 그 글의 실제 menuId가 함께 담겨, 본문 조회에 쓴다.
_ALL_ARTICLES_MENU = 0
_PAGE_SIZE = 50

# 카페 홈 HTML에서 clubId(=cafeId)를 뽑는 패턴(레거시 전역변수 / SPA JSON 두 형태).
_CLUB_ID_RE = re.compile(r"""g_sClubId\s*=\s*["'](\d+)["']""")
_CLUB_ID_JSON_RE = re.compile(r'"cafeId"\s*:\s*"?(\d+)"?')

# writeDate가 초 단위로 오는 경우를 감지하는 경계(ms면 이 값 이상).
_MS_THRESHOLD = 1_000_000_000_000


class NaverCafeClient:
    """카페 내부 API로 글 목록·본문을 가져오는 클라이언트.

    Args:
        ref: 입력에서 해석한 카페 참조(clubId/클럽 URL/menuId/articleId).
        cookie: 세션 쿠키 문자열(``"NID_AUT=...; NID_SES=..."``). 로그인/등급
            제한 게시판 접근에 필요하다. 공개 게시판만 받을 때는 없어도 된다.
        menu_id: 특정 게시판만 대상으로 할 때의 menuId(``ref.menu_id``보다 우선).
        delay: 요청 사이 대기 시간(초).
        max_retries: 실패 시 최대 재시도 횟수.
        timeout: 단일 요청 타임아웃(초).
    """

    def __init__(
        self,
        ref: CafeReference,
        *,
        cookie: str | None = None,
        menu_id: int | None = None,
        delay: float = 0.5,
        max_retries: int = 3,
        timeout: float = 20.0,
    ) -> None:
        self.ref = ref
        self.delay = delay
        self.max_retries = max_retries
        self._cafe_id: int | None = ref.cafe_id
        self._menu_id = menu_id if menu_id is not None else ref.menu_id
        # articleId → menuId. 목록을 순회하며 채워, 본문 조회 때 menuId를 넘긴다.
        self._menu_of: dict[int, int] = {}
        # 단일 글 모드 등에서 본문을 한 번만 받도록 article dict를 캐시한다.
        self._article_cache: dict[int, dict[str, object]] = {}

        headers = {
            "User-Agent": _MOBILE_UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": self._referer(),
        }
        cookie = (cookie or "").strip()
        if cookie:
            headers["Cookie"] = cookie
            if "NID_AUT" not in cookie:
                logger.warning(
                    "쿠키에 NID_AUT가 없습니다. 로그인 세션이 아닐 수 있어 "
                    "등급 제한 게시판 접근이 실패할 수 있습니다."
                )
        self._client = httpx.Client(headers=headers, timeout=timeout, follow_redirects=True)

    def __enter__(self) -> NaverCafeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _referer(self) -> str:
        if self.ref.club_url:
            return _CAFE_HOME_URL.format(club=self.ref.club_url)
        return "https://cafe.naver.com/"

    def _get(self, url: str, params: dict[str, object] | None = None) -> httpx.Response:
        return get_with_retry(
            self._client,
            url,
            params,
            delay=self.delay,
            max_retries=self.max_retries,
            logger=logger,
            fatal=self._fatal,
        )

    def _fatal(self, exc: httpx.HTTPStatusError) -> LoginRequired | None:
        """401/403은 재시도해도 소용없는 인증/권한 문제이므로 즉시 안내한다."""
        status = exc.response.status_code
        if status in (401, 403):
            return LoginRequired(
                f"로그인/권한이 필요합니다(HTTP {status}). "
                "유효한 NID_AUT/NID_SES 쿠키를 --cookie로 주입하세요."
            )
        return None

    # -- clubId 해석 -----------------------------------------------------
    @property
    def cafe_id(self) -> int:
        """숫자 clubId. 아직 해석하지 않았으면 카페 홈에서 찾아 캐시한다."""
        if self._cafe_id is None:
            self._cafe_id = self._resolve_cafe_id()
        return self._cafe_id

    def _resolve_cafe_id(self) -> int:
        if self.ref.club_url is None:
            raise CafeNotFound("clubId도 클럽 URL도 없습니다.")
        resp = self._get(_CAFE_HOME_URL.format(club=self.ref.club_url))
        match = _CLUB_ID_RE.search(resp.text) or _CLUB_ID_JSON_RE.search(resp.text)
        if match is None:
            raise CafeNotFound(self.ref.club_url)
        cafe_id = int(match.group(1))
        logger.info("clubId 해석: %s → %d", self.ref.club_url, cafe_id)
        return cafe_id

    # -- 글 목록 ---------------------------------------------------------
    def iter_post_meta(self) -> Iterator[PostMeta]:
        """대상 글의 메타데이터를 최신→과거 순으로 순회한다.

        단일 글 참조면 그 글 하나만, ``--menu`` 지정이 있으면 그 게시판만, 아니면
        전체글(:data:`_ALL_ARTICLES_MENU`)을 대상으로 한다. 전체글은 게시판을
        가로질러 카페의 모든 글(읽기 권한 내)을 최신순으로 주며, 각 글 항목에 담긴
        그 글의 실제 menuId를 본문 조회용으로 기록한다.
        """
        cafe_id = self.cafe_id
        if self.ref.article_id is not None:
            article = self._fetch_article(self.ref.article_id)
            yield self._meta_from_article(self.ref.article_id, article)
            return
        menu_id = self._menu_id if self._menu_id is not None else _ALL_ARTICLES_MENU
        yield from self._iter_menu_articles(cafe_id, menu_id)

    def _iter_menu_articles(self, cafe_id: int, menu_id: int) -> Iterator[PostMeta]:
        """한 게시판(전체글 0 포함)의 글을 페이지네이션하며 순회한다.

        빈 페이지, 또는 새 글이 하나도 없는 페이지(API가 같은 페이지를 반복하는
        비정상 상황)를 만나면 종료한다(블로그와 동일한 안전장치). 이미 방출한 글은
        건너뛰고, 각 글의 실제 menuId를 본문 조회용으로 :attr:`_menu_of`에 기록한다.
        """
        page = 1
        while True:
            resp = self._get(
                _ARTICLE_LIST_URL.format(cafe_id=cafe_id, menu_id=menu_id),
                params={
                    "page": page,
                    "pageSize": _PAGE_SIZE,
                    "sortBy": "TIME",
                    "viewType": "L",
                },
            )
            articles = _extract_articles(resp.json())
            logger.debug("게시글 목록 menu=%d page=%d: %d건", menu_id, page, len(articles))
            if not articles:
                return
            new_count = 0
            for item in articles:
                meta = _article_meta(item)
                if meta is None or meta.log_no in self._menu_of:
                    continue
                # 전체글(0)이면 항목마다 실제 menuId가 다르다. 본문 조회에 그 값을 쓴다.
                real_menu = _article_menu_id(item)
                self._menu_of[meta.log_no] = real_menu if real_menu is not None else menu_id
                new_count += 1
                yield meta
            if new_count == 0:
                return
            page += 1

    # -- 본문 ------------------------------------------------------------
    def fetch_post_html(self, article_id: int) -> str:
        """글 본문 HTML(``contentHtml``)을 가져온다."""
        article = self._fetch_article(article_id)
        content = article.get("contentHtml") or article.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            raise ParseError(f"카페 글 본문(contentHtml)이 비어 있습니다: articleId={article_id}")
        return content

    def _fetch_article(self, article_id: int) -> dict[str, object]:
        """v3 글 API를 호출해 ``article`` 객체를 돌려준다(캐시 적용)."""
        if article_id in self._article_cache:
            return self._article_cache[article_id]
        resp = self._get(
            _ARTICLE_URL.format(cafe_id=self.cafe_id, article_id=article_id),
            params={
                "query": "",
                "menuId": self._menu_for(article_id),
                "boardType": "L",
                "useCafeId": "true",
                "requestFrom": "A",
            },
        )
        article = self._article_from(resp.json(), article_id)
        self._article_cache[article_id] = article
        return article

    def _menu_for(self, article_id: int) -> str:
        """본문 조회에 넘길 menuId(모르면 빈 문자열)."""
        menu_id = self._menu_of.get(article_id, self._menu_id)
        return str(menu_id) if menu_id is not None else ""

    def _article_from(self, data: object, article_id: int) -> dict[str, object]:
        """응답에서 ``article`` 객체를 꺼내거나, 로그인/삭제 오류를 구분해 던진다."""
        result = _unwrap(data)
        article = result.get("article")
        if isinstance(article, dict):
            return article
        _raise_for_article_error(data, article_id)
        raise ParseError(f"카페 글 응답에서 article을 찾을 수 없습니다: articleId={article_id}")

    def _meta_from_article(self, article_id: int, article: dict[str, object]) -> PostMeta:
        """단일 글 모드에서 v3 글 응답으로 :class:`PostMeta`를 만든다."""
        menu = article.get("menu")
        if isinstance(menu, dict):
            menu_id = _to_int(menu.get("id"))
            if menu_id is not None:
                self._menu_of[article_id] = menu_id
        return PostMeta(
            log_no=article_id,
            title=str(article.get("subject") or "").strip(),
            add_date_ms=_write_ms(article),
            is_anniversary=False,
        )

    def post_url(self, article_id: int) -> str:
        """사람이 보는 글 주소."""
        if self.ref.club_url:
            return f"{_CAFE_HOME_URL.format(club=self.ref.club_url)}/{article_id}"
        return _SPA_ARTICLE_URL.format(cafe_id=self.cafe_id, article_id=article_id)


def _unwrap(data: object) -> dict[str, object]:
    """응답 봉투(message.result / result / 최상위)를 벗겨 실제 데이터 dict를 돌려준다."""
    if not isinstance(data, dict):
        return {}
    message = data.get("message")
    if isinstance(message, dict) and isinstance(message.get("result"), dict):
        return message["result"]
    result = data.get("result")
    if isinstance(result, dict):
        return result
    return data


def _extract_articles(data: object) -> list[dict[str, object]]:
    result = _unwrap(data)
    articles = result.get("articleList") or result.get("articles") or []
    return [a for a in articles if isinstance(a, dict)]


def _article_node(item: dict[str, object]) -> dict[str, object]:
    """목록 항목의 실제 데이터 dict를 돌려준다.

    boardlist-api는 각 항목을 ``{"type": "ARTICLE", "item": {...}}``로 중첩하고,
    구형/평면 응답은 dict를 그대로 쓴다. 둘 다 대응한다.
    """
    inner = item.get("item")
    return inner if isinstance(inner, dict) else item


def _article_menu_id(item: dict[str, object]) -> int | None:
    """목록 항목에서 그 글의 실제 menuId를 뽑는다(전체글 조회 시 본문 조회용)."""
    return _to_int(_article_node(item).get("menuId"))


def _article_meta(item: dict[str, object]) -> PostMeta | None:
    """목록 항목 하나를 :class:`PostMeta`로 변환한다(식별 불가면 None)."""
    node = _article_node(item)
    article_id = _to_int(node.get("articleId") or node.get("articleid"))
    if article_id is None:
        return None
    title = str(node.get("subject") or node.get("title") or "").strip()
    return PostMeta(
        log_no=article_id,
        title=title,
        add_date_ms=_write_ms(node),
        is_anniversary=False,
    )


def _write_ms(node: dict[str, object]) -> int:
    """작성 시각을 epoch ms로 돌려준다(찾지 못하면 0)."""
    for key in ("writeDateTimestamp", "writeDate", "addDate", "menuArticleWriteDate"):
        millis = _coerce_ms(node.get(key))
        if millis is not None:
            return millis
    return 0


def _coerce_ms(value: object) -> int | None:
    """숫자(정수/실수/숫자 문자열)나 ISO 문자열을 epoch ms로 변환한다(불가면 None).

    비공식 API는 작성 시각을 정수 ms/초, 숫자 문자열, ISO 문자열 등 여러 형태로
    줄 수 있어 모두 방어적으로 수용한다. 초 단위로 보이면 ms로 보정한다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        millis = int(value)
        return millis if millis >= _MS_THRESHOLD else millis * 1000
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            millis = int(text)
            return millis if millis >= _MS_THRESHOLD else millis * 1000
        return _parse_iso_ms(text)
    return None


def _parse_iso_ms(text: str) -> int | None:
    """ISO 형식(``2024-01-02 10:00:00`` 등) 문자열을 epoch ms로 변환한다.

    시간대가 없으면 KST로 간주한다(네이버 카페는 KST 기준). 파싱 불가면 None.
    """
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return int(parsed.timestamp() * 1000)


def _to_int(value: object) -> int | None:
    """정수로 변환 가능하면 int, 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _raise_for_article_error(data: object, article_id: int) -> None:
    """응답의 오류 코드/메시지가 로그인·권한 문제면 :class:`LoginRequired`로 던진다."""
    error = {}
    if isinstance(data, dict):
        error = data.get("error") or _unwrap(data).get("error") or {}
    if not isinstance(error, dict):
        return
    blob = f"{error.get('code', '')} {error.get('msg', '') or error.get('message', '')}".lower()
    login_signals = ("login", "로그인", "auth", "권한", "가입", "member", "permission", "forbidden")
    if any(signal in blob for signal in login_signals):
        raise LoginRequired(
            f"로그인/권한이 필요한 글입니다(articleId={article_id}). "
            "유효한 NID_AUT/NID_SES 쿠키를 --cookie로 주입하세요."
        )
