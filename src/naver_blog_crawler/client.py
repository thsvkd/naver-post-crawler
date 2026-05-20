"""네이버 모바일 블로그 HTTP 클라이언트.

post-list JSON API로 글 목록을, PostView HTML로 본문을 가져온다.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx

from .errors import FetchError, ParseError
from .models import PostMeta

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
)

# 네이버 모바일 블로그 엔드포인트.
_BASE = "https://m.blog.naver.com"
_POST_LIST_PATH = "/api/blogs/{blog_id}/post-list"
_POST_VIEW_PATH = "/PostView.naver"

# 전체글은 카테고리 0. post-list의 한 페이지 크기.
_ALL_POSTS_CATEGORY_NO = 0
_PAGE_SIZE = 30


class NaverBlogClient:
    """모바일 블로그 API/HTML을 가져오는 얇은 래퍼.

    Args:
        blog_id: 블로그 아이디 (예: ``winter9377``).
        delay: 요청 사이 대기 시간(초). 서버 부하를 줄이기 위한 예의.
        max_retries: 실패 시 최대 재시도 횟수.
        timeout: 단일 요청 타임아웃(초).
    """

    def __init__(
        self,
        blog_id: str,
        *,
        delay: float = 0.5,
        max_retries: int = 3,
        timeout: float = 20.0,
    ) -> None:
        self.blog_id = blog_id
        self.delay = delay
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=_BASE,
            headers={
                "User-Agent": _MOBILE_UA,
                "Referer": f"{_BASE}/{blog_id}",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def __enter__(self) -> NaverBlogClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, object] | None = None) -> httpx.Response:
        """딜레이·지수 백오프 재시도를 적용해 GET 요청을 수행한다.

        재시도해도 의미 없는 4xx(요청 자체 오류)는 429(Too Many Requests)를
        빼고 즉시 중단한다. 일시적 장애·5xx·429는 백오프를 두고 재시도한다.
        """
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            if self.delay:
                time.sleep(self.delay)
            try:
                resp = self._client.get(path, params=params)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                if 400 <= status < 500 and status != 429:
                    break
            except httpx.HTTPError as exc:
                last_exc = exc
            # 마지막 시도가 아니면 점증 대기 후 재시도한다.
            if attempt < self.max_retries - 1:
                time.sleep(self.delay * (2**attempt))
        url = str(self._client.build_request("GET", path, params=params).url)
        raise FetchError(url, attempts=self.max_retries, cause=last_exc)

    def iter_post_meta(self) -> Iterator[PostMeta]:
        """전체글의 메타데이터를 최신→과거 순으로 순회한다.

        post-list 응답의 ``totalCount``는 0으로 고정되어 신뢰할 수 없으므로,
        ``items``가 빈 페이지를 만날 때까지 페이지를 증가시킨다. 또한 API가
        같은 페이지를 계속 돌려주는 비정상 상황에서도 무한 루프에 빠지지 않도록,
        새로운 글이 하나도 없으면(이미 본 logNo뿐이면) 종료한다.
        """
        seen: set[int] = set()
        page = 1
        while True:
            resp = self._get(
                _POST_LIST_PATH.format(blog_id=self.blog_id),
                params={
                    "categoryNo": _ALL_POSTS_CATEGORY_NO,
                    "itemCount": _PAGE_SIZE,
                    "page": page,
                },
            )
            items = resp.json().get("result", {}).get("items", [])
            if not items:
                return
            new_count = 0
            for item in items:
                meta = _parse_meta(item)
                if meta.log_no in seen:
                    continue
                seen.add(meta.log_no)
                new_count += 1
                yield meta
            if new_count == 0:
                return
            page += 1

    def fetch_post_html(self, log_no: int) -> str:
        """글 본문 페이지(PostView)의 HTML을 가져온다."""
        resp = self._get(
            _POST_VIEW_PATH,
            params={"blogId": self.blog_id, "logNo": log_no},
        )
        return resp.text

    def post_url(self, log_no: int) -> str:
        """사람이 보는 글 주소."""
        return f"{_BASE}/{self.blog_id}/{log_no}"


def _parse_meta(item: dict[str, object]) -> PostMeta:
    """post-list 항목 하나를 :class:`PostMeta`로 변환한다.

    JSON 응답 구조가 바뀌어 필수 필드가 없거나 숫자가 아니면, 원인이 분명한
    :class:`ParseError`로 감싸 던진다(raw KeyError/ValueError로 새지 않도록).
    """
    return PostMeta(
        log_no=_require_int(item, "logNo"),
        title=str(item.get("titleWithInspectMessage", "")).strip(),
        add_date_ms=_require_int(item, "addDate"),
        # thisDayPostInfo가 채워진 글은 "N년 전 오늘" 자동 노출 글이다.
        is_anniversary=bool(item.get("thisDayPostInfo")),
    )


def _require_int(item: dict[str, object], key: str) -> int:
    """post-list 항목에서 정수 필드를 안전하게 꺼낸다."""
    if key not in item:
        raise ParseError(f"post-list 항목에 '{key}' 필드가 없습니다: {item!r}")
    try:
        return int(item[key])  # type: ignore[call-overload]
    except (TypeError, ValueError) as exc:
        raise ParseError(
            f"post-list '{key}' 값을 정수로 변환할 수 없습니다: {item[key]!r}"
        ) from exc
