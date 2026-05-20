"""resolve_blog_id의 ID/URL 인식 테스트."""

from __future__ import annotations

import pytest

from naver_blog_crawler.blog_id import resolve_blog_id
from naver_blog_crawler.errors import InvalidBlogReference


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("winter9377", "winter9377"),
        ("  winter9377  ", "winter9377"),
        ("https://m.blog.naver.com/winter9377", "winter9377"),
        ("https://blog.naver.com/winter9377", "winter9377"),
        ("m.blog.naver.com/winter9377", "winter9377"),
        ("https://blog.naver.com/winter9377/223189475037", "winter9377"),
        ("https://m.blog.naver.com/winter9377/224291762552?referrerCode=1", "winter9377"),
        ("https://m.blog.naver.com/PostView.naver?blogId=winter9377&logNo=1", "winter9377"),
    ],
)
def test_resolve_blog_id(value: str, expected: str) -> None:
    assert resolve_blog_id(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "foo/bar", "https://m.blog.naver.com/PostView.naver"])
def test_resolve_blog_id_invalid(value: str) -> None:
    with pytest.raises(InvalidBlogReference):
        resolve_blog_id(value)
