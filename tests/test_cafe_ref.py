"""카페 참조(URL) 해석·라우팅 판별 테스트."""

from __future__ import annotations

import pytest

from naver_post_crawler.cafe_ref import (
    CafeReference,
    is_cafe_reference,
    resolve_cafe_reference,
)
from naver_post_crawler.errors import InvalidCafeReference


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://cafe.naver.com/steamindiegame", True),
        ("cafe.naver.com/steamindiegame", True),
        ("https://m.cafe.naver.com/ca/steamindiegame", True),
        ("winter9377", False),
        ("https://m.blog.naver.com/winter9377", False),
    ],
)
def test_is_cafe_reference(value: str, expected: bool) -> None:
    assert is_cafe_reference(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            # Test-7: f-e 접두 + menus + viewType 쿼리 파라미터
            "https://cafe.naver.com/f-e/cafes/27999207/menus/3?viewType=L",
            CafeReference(cafe_id=27999207, menu_id=3),
        ),
        (
            # Test-8: f-e 접두 + menus(쿼리 없음)
            "https://cafe.naver.com/f-e/cafes/27999207/menus/4",
            CafeReference(cafe_id=27999207, menu_id=4),
        ),
        (
            "https://cafe.naver.com/steamindiegame",
            CafeReference(club_url="steamindiegame"),
        ),
        (
            "https://cafe.naver.com/steamindiegame/12345",
            CafeReference(club_url="steamindiegame", article_id=12345),
        ),
        (
            "https://cafe.naver.com/ca-fe/cafes/29434212/menus/5",
            CafeReference(cafe_id=29434212, menu_id=5),
        ),
        (
            "https://cafe.naver.com/f-e/cafes/29434212/articles/777?menuid=5",
            CafeReference(cafe_id=29434212, menu_id=5, article_id=777),
        ),
        (
            "https://cafe.naver.com/ArticleList.nhn?search.clubid=29434212&search.menuid=8",
            CafeReference(cafe_id=29434212, menu_id=8),
        ),
        (
            "https://m.cafe.naver.com/ca/steamindiegame",
            CafeReference(club_url="steamindiegame"),
        ),
        # 일반 단어를 vanity(클럽) URL로 쓰는 카페도 정상 인식돼야 한다(예약어 하드코딩 금지).
        (
            "https://cafe.naver.com/gaming",
            CafeReference(club_url="gaming"),
        ),
        (
            "https://cafe.naver.com/member/9999",
            CafeReference(club_url="member", article_id=9999),
        ),
    ],
)
def test_resolve_cafe_reference(value: str, expected: CafeReference) -> None:
    assert resolve_cafe_reference(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "https://blog.naver.com/winter9377",
        "https://cafe.naver.com/",
        "https://cafe.naver.com/f-e/cafes/",
    ],
)
def test_resolve_cafe_reference_invalid(value: str) -> None:
    with pytest.raises(InvalidCafeReference):
        resolve_cafe_reference(value)
