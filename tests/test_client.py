"""post-list 항목 → PostMeta 변환, 날짜 변환, 존재하지 않는 블로그 처리 테스트."""

from __future__ import annotations

import httpx
import pytest

from naver_post_crawler.client import NaverBlogClient, _is_blog_missing, _parse_meta
from naver_post_crawler.errors import BlogNotFound, ParseError
from naver_post_crawler.models import PostMeta

_NOT_EXIST_BODY = {
    "isSuccess": False,
    "error": {"code": "not_exist_blog", "message": "해당 블로그가 없습니다.", "details": ""},
}


def test_parse_meta_extracts_fields() -> None:
    item = {
        "logNo": "223189475037",
        "titleWithInspectMessage": "  맥쿼리 인프라  ",
        "addDate": 1692576000000,
        "thisDayPostInfo": None,
    }
    meta = _parse_meta(item)
    assert meta.log_no == 223189475037
    assert meta.title == "맥쿼리 인프라"
    assert meta.is_anniversary is False


def test_parse_meta_flags_anniversary() -> None:
    item = {
        "logNo": 1,
        "titleWithInspectMessage": "[1년 전 오늘] ...",
        "addDate": 1692576000000,
        "thisDayPostInfo": {"logNo": 999},
    }
    assert _parse_meta(item).is_anniversary is True


def test_parse_meta_missing_field_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _parse_meta({"titleWithInspectMessage": "x", "addDate": 1})


def test_parse_meta_non_numeric_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _parse_meta({"logNo": "not-a-number", "addDate": 1})


def test_post_meta_date_is_kst() -> None:
    # 1692576000000 ms = 2023-08-21 00:00 UTC = 09:00 KST
    meta = PostMeta(log_no=1, title="t", add_date_ms=1692576000000, is_anniversary=False)
    assert meta.date_str == "2023-08-21"
    assert meta.written_at.hour == 9


def test_is_blog_missing_detects_not_exist_blog() -> None:
    assert _is_blog_missing(httpx.Response(404, json=_NOT_EXIST_BODY)) is True


def test_is_blog_missing_false_for_other_responses() -> None:
    # 다른 4xx 에러 코드나 JSON이 아닌 본문은 일반 실패 처리에 맡긴다.
    other_error = httpx.Response(404, json={"isSuccess": False, "error": {"code": "x"}})
    assert _is_blog_missing(other_error) is False
    assert _is_blog_missing(httpx.Response(404, text="<html>not json</html>")) is False


def _client_with_handler(handler: object, **kw: object) -> NaverBlogClient:
    """실제 네트워크 대신 MockTransport로 응답하는 클라이언트를 만든다."""
    client = NaverBlogClient("adsfsafasdf", delay=0, **kw)  # type: ignore[arg-type]
    client._client.close()  # __init__이 만든 실제 클라이언트는 닫고 교체한다
    client._client = httpx.Client(
        base_url="https://m.blog.naver.com",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return client


def test_iter_post_meta_raises_blog_not_found_without_retry() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404, json=_NOT_EXIST_BODY)

    client = _client_with_handler(handler, max_retries=3)
    try:
        with pytest.raises(BlogNotFound) as excinfo:
            list(client.iter_post_meta())
    finally:
        client.close()

    assert "adsfsafasdf" in str(excinfo.value)
    # 재시도해도 의미 없으므로 단 한 번만 요청한다(max_retries=3이어도).
    assert len(calls) == 1
