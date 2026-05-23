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

# 구버전 SE 3.0 글(주로 [공유] 글). se-main-container가 없고, 본문이 두
# se_doc_viewer 블록에 나뉘며 제목도 se_documentTitle 컴포넌트로 들어 있다.
LEGACY_POST = """
<div id="viewTypeSelector" class="post_ct se3_view">
  <div class="se_doc_viewer se_body_wrap se_m">
    <div class="se_component se_documentTitle documentTitle_blog">
      <div class="se_textarea"><h3 class="se_textarea">제목은 본문에서 빠져야 한다</h3></div>
    </div>
    <div class="se_component_wrap sect_dsc __se_component_area">
      <div class="se_component se_paragraph default">
        <div class="se_textView">
          <p class="se_textarea">첫 줄.<br> <br>둘째 문단.</p>
        </div>
      </div>
    </div>
  </div>
  <div class="se_doc_viewer se_body_wrap">
    <div class="se_component se_oglink default">
      <div class="se_viewArea se_og_wrap">
        <a class="se_og_box __se_link" href="https://m.blog.naver.com/other/12345">
          <div class="se_og_txt">
            <div class="se_og_tit">원문 글 제목</div>
            <div class="se_og_desc">설명 미리보기</div>
          </div>
        </a>
      </div>
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


def test_legacy_post_text_with_linebreaks() -> None:
    body = parse_post_body(LEGACY_POST)
    assert body.has_content is True
    assert "첫 줄." in body.text
    assert "둘째 문단." in body.text
    # <br> 두 개는 빈 줄(문단 구분)로 살아야 한다.
    assert "첫 줄.\n\n둘째 문단." in body.text


def test_legacy_oglink_rendered_as_placeholder() -> None:
    body = parse_post_body(LEGACY_POST)
    assert "[링크: 원문 글 제목 https://m.blog.naver.com/other/12345]" in body.text


def test_legacy_document_title_is_excluded_from_body() -> None:
    body = parse_post_body(LEGACY_POST)
    assert "제목은 본문에서 빠져야 한다" not in body.text


# 제목 컴포넌트만 있는 구버전 글: 본문 없음으로 판정되어야 한다.
LEGACY_TITLE_ONLY = """
<div class="se3_view">
  <div class="se_doc_viewer">
    <div class="se_component se_documentTitle">
      <div class="se_textarea">제목뿐</div>
    </div>
  </div>
</div>
"""

# 종류를 모르는 컴포넌트와 본문 텍스트가 함께 있는 구버전 글.
LEGACY_UNKNOWN_COMPONENT = """
<div class="se3_view">
  <div class="se_doc_viewer">
    <div class="se_component se_paragraph">
      <div class="se_textView"><p class="se_textarea">본문 문단.</p></div>
    </div>
    <div class="se_component se_unknownWidget">알 수 없는 컴포넌트 텍스트</div>
  </div>
</div>
"""


def test_legacy_title_only_post_has_no_content() -> None:
    body = parse_post_body(LEGACY_TITLE_ONLY)
    assert body.has_content is False
    assert body.text == ""


def test_legacy_unknown_component_text_is_preserved() -> None:
    body = parse_post_body(LEGACY_UNKNOWN_COMPONENT)
    # 본문 문단이 있으니 내용 있는 글이고, 모르는 컴포넌트 텍스트도 보존된다.
    assert body.has_content is True
    assert "본문 문단." in body.text
    assert "알 수 없는 컴포넌트 텍스트" in body.text
