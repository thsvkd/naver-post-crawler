"""parse_post_body의 렌더링·빈 글 판정 테스트."""

from __future__ import annotations

import pytest

from naver_blog_crawler.errors import ParseError
from naver_blog_crawler.parser import parse_post_body

FULL_POST = """
<div class="se-main-container">
  <div class="se-component se-text">
    <div class="se-text-paragraph">안녕하세요.</div>
    <div class="se-text-paragraph">​</div>
    <div class="se-text-paragraph">두 번째 문단.</div>
  </div>
  <div class="se-component se-image">
    <img data-lazy-src="https://img.example/p.png?type=w800" src="data:placeholder">
  </div>
  <div class="se-component se-oglink">
    <a href="https://naver.me/abc">
      <strong class="se-oglink-title">링크 제목</strong>
    </a>
  </div>
  <div class="se-component se-quotation">
    <div class="se-quotation-container">인용한 문장</div>
  </div>
  <div id="ad-bottom-portal"></div>
  <div class="se-component se-text">
    <div class="se-text-paragraph">광고 이후 문단은 무시되어야 한다.</div>
  </div>
</div>
"""

ANNIVERSARY_ONLY = """
<div class="se-main-container">
  <div class="se-component se-anniversarySection">
    <div class="se-text-paragraph">1년 전 오늘</div>
  </div>
</div>
"""

# 컴포넌트가 레이아웃 div로 감싸인 경우(직계 자식이 아닌 경우).
NESTED_LAYOUT = """
<div class="se-main-container">
  <div class="se-section">
    <div class="se-component se-text">
      <div class="se-text-paragraph">감싸인 본문도 추출되어야 한다.</div>
    </div>
  </div>
</div>
"""


def test_renders_text_paragraphs_with_blank_line() -> None:
    body = parse_post_body(FULL_POST)
    assert "안녕하세요." in body.text
    assert "두 번째 문단." in body.text
    assert body.has_content is True


def test_image_rendered_as_placeholder_with_lazy_src() -> None:
    body = parse_post_body(FULL_POST)
    assert "[이미지: https://img.example/p.png?type=w800]" in body.text


def test_oglink_rendered_as_placeholder() -> None:
    body = parse_post_body(FULL_POST)
    assert "[링크: 링크 제목 https://naver.me/abc]" in body.text


def test_quotation_is_prefixed() -> None:
    body = parse_post_body(FULL_POST)
    assert "> 인용한 문장" in body.text


def test_content_after_ad_portal_is_ignored() -> None:
    body = parse_post_body(FULL_POST)
    assert "광고 이후 문단" not in body.text


def test_anniversary_only_post_has_no_content() -> None:
    body = parse_post_body(ANNIVERSARY_ONLY)
    assert body.has_content is False
    assert body.text == ""


def test_nested_layout_components_are_extracted() -> None:
    body = parse_post_body(NESTED_LAYOUT)
    assert body.has_content is True
    assert "감싸인 본문도 추출되어야 한다." in body.text


def test_missing_container_raises() -> None:
    with pytest.raises(ParseError):
        parse_post_body("<html><body>본문 없음</body></html>")
