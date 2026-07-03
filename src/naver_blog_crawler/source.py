"""글 소스(블로그·카페)가 만족해야 하는 공통 계약.

:class:`~naver_blog_crawler.crawler.Crawler`는 이 프로토콜만 알면 되므로,
블로그(:class:`~naver_blog_crawler.client.NaverBlogClient`)와
카페(:class:`~naver_blog_crawler.cafe_client.NaverCafeClient`) 클라이언트를
같은 오케스트레이션으로 다룰 수 있다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from .models import PostMeta


@runtime_checkable
class PostSource(Protocol):
    """글 목록 메타·본문·주소를 제공하는 소스."""

    def iter_post_meta(self) -> Iterator[PostMeta]:
        """전체 글의 메타데이터를 최신→과거 순으로 순회한다."""
        ...

    def fetch_post_html(self, post_id: int) -> str:
        """글 한 건의 본문 HTML(파서가 해석할 조각)을 가져온다."""
        ...

    def post_url(self, post_id: int) -> str:
        """사람이 보는 글 주소."""
        ...
