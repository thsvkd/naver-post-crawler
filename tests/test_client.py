"""post-list 항목 → PostMeta 변환 및 날짜 변환 테스트."""

from __future__ import annotations

import pytest

from naver_blog_crawler.client import _parse_meta
from naver_blog_crawler.errors import ParseError
from naver_blog_crawler.models import PostMeta


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
