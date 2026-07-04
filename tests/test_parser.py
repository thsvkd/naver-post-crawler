"""parse_post_body의 렌더링·빈 글 판정 테스트."""

from __future__ import annotations

import pytest

from naver_post_crawler.errors import ParseError
from naver_post_crawler.parser import parse_cafe_body, parse_post_body

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


# --- 카페 본문(parse_cafe_body) -------------------------------------------------

# 카페 스마트에디터 ONE 글: 블로그와 동일한 se-main-container 경로를 재사용한다.
CAFE_SE_ONE = """
<div class="se-main-container">
  <div class="se-component se-text">
    <div class="se-text-paragraph">카페 본문 첫 줄.</div>
  </div>
  <div class="se-component se-image">
    <img data-lazy-src="https://cafeptthumb/x.jpg" src="data:placeholder">
  </div>
</div>
"""

# 스마트에디터가 아닌 단순 HTML 본문(SE 2.0/일반). 평문 폴백으로 처리된다.
CAFE_PLAIN = (
    "<div>첫 문단입니다.</div>"
    "<p>둘째 문단.</p>"
    '<img data-lazy-src="https://img/photo.png" src="data:abc">'
    "셋째 줄<br>넷째 줄"
)


def test_cafe_se_one_reuses_smarteditor_path() -> None:
    body = parse_cafe_body(CAFE_SE_ONE)
    assert body.has_content is True
    assert "카페 본문 첫 줄." in body.text
    assert "[이미지: https://cafeptthumb/x.jpg]" in body.text


def test_cafe_plain_fallback_preserves_text_and_images_in_order() -> None:
    body = parse_cafe_body(CAFE_PLAIN)
    assert body.has_content is True
    assert "첫 문단입니다." in body.text
    assert "둘째 문단." in body.text
    # data: URI 플레이스홀더가 아니라 실제 이미지 URL을 표기하고, 순서를 보존한다.
    assert "[이미지: https://img/photo.png]" in body.text
    assert body.text.index("둘째 문단.") < body.text.index("[이미지: https://img/photo.png]")
    assert body.text.index("[이미지: https://img/photo.png]") < body.text.index("셋째 줄")
    # <br>은 줄바꿈으로 보존된다.
    assert "셋째 줄\n넷째 줄" in body.text


def test_cafe_plain_image_prefers_real_url_over_data_uri_regardless_of_order() -> None:
    # data-lazy-src가 placeholder data: URI이고 실제 URL이 src에 있는 역순 배치에서도,
    # 속성 경계를 지켜 실제 이미지 URL을 살려야 한다(부분일치로 유실되면 안 됨).
    html = '<img data-lazy-src="data:image/gif;base64,AAAA" src="https://real/img.png">'
    body = parse_cafe_body(html)
    assert "[이미지: https://real/img.png]" in body.text
    assert "data:image" not in body.text


def test_cafe_empty_body_has_no_content_without_raising() -> None:
    # 블로그 parse_post_body와 달리 컨테이너가 없어도 예외를 던지지 않는다.
    body = parse_cafe_body("<div></div>")
    assert body.has_content is False
    assert body.text == ""


def test_cafe_legacy_se3_fallback() -> None:
    body = parse_cafe_body(LEGACY_POST)
    assert body.has_content is True
    assert "첫 줄." in body.text
