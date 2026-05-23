"""PostView HTML의 ``se-main-container``를 텍스트로 렌더링한다.

요소 개수 같은 휴리스틱 대신 스마트에디터 모듈(se-component)의 종류로
콘텐츠를 판별·렌더링한다. 텍스트 외 요소(이미지·링크카드·인용구)는
플레이스홀더로 표기해 본문의 순서와 맥락을 보존한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from selectolax.parser import HTMLParser, Node

from .enums import (
    AD_PORTAL_ID,
    CONTENT_MODULE_CLASSES,
    LEGACY_CONTAINER_SELECTOR,
    LEGACY_CONTENT_MODULE_CLASSES,
    MAIN_CONTAINER_SELECTOR,
    LegacyModuleClass,
    ModuleClass,
)
from .errors import ParseError

# 네이버가 빈 줄·공백에 쓰는 제로 폭 공백과 줄 끝 정리용 정규식.
_ZERO_WIDTH = re.compile(r"[​﻿]")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")
# 구버전 본문은 <br>로 줄을 나누므로 텍스트 추출 전에 줄바꿈으로 치환한다.
_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
# 구버전 se_component 마커 클래스(언더스코어식).
_LEGACY_COMPONENT_CLASS = "se_component"


@dataclass(frozen=True, slots=True)
class ParsedBody:
    """렌더링된 본문과 콘텐츠 보유 여부."""

    text: str
    has_content: bool


def parse_post_body(html: str) -> ParsedBody:
    """PostView HTML에서 본문 텍스트를 추출한다.

    스마트에디터 ONE(``se-main-container``)을 먼저 시도하고, 없으면 구버전
    SE 3.0(``se3_view``) 포맷으로 폴백한다. 둘 다 없을 때만 실패로 본다.

    Args:
        html: PostView 페이지 전체 HTML.

    Returns:
        렌더링된 본문 텍스트와, 실제 사용자 콘텐츠 모듈이 하나라도 있는지 여부.

    Raises:
        ParseError: 본문 컨테이너(se-main-container/se3_view)를 모두 찾지 못한 경우.
    """
    tree = HTMLParser(html)
    container = tree.css_first(MAIN_CONTAINER_SELECTOR)
    if container is not None:
        return _parse_se_one(container)

    legacy = tree.css_first(LEGACY_CONTAINER_SELECTOR)
    if legacy is not None:
        return _parse_legacy(legacy)

    raise ParseError("본문 컨테이너(se-main-container/se3_view)를 찾을 수 없습니다.")


def _parse_se_one(container: Node) -> ParsedBody:
    """스마트에디터 ONE 본문(se-main-container)을 렌더링한다."""
    blocks: list[str] = []
    has_content = False
    for component in _iter_components(container):
        module = _module_of(component)
        if module is None:
            continue
        if module in CONTENT_MODULE_CLASSES:
            has_content = True
        rendered = _render(module, component)
        if rendered:
            blocks.append(rendered)

    return ParsedBody(text=_finalize("\n\n".join(blocks)), has_content=has_content)


def _iter_components(container: Node) -> Iterator[Node]:
    """본문 컨테이너의 se-component를 문서 순서대로, 광고 포털 직전까지 순회한다."""
    return _iter_components_by_marker(container, "se-component", stop_at_ad_portal=True)


def _iter_components_by_marker(
    container: Node, marker: str, *, stop_at_ad_portal: bool
) -> Iterator[Node]:
    """``marker`` 클래스를 가진 컴포넌트를 문서 순서대로 순회한다.

    네이버가 컴포넌트를 레이아웃 div로 감싸는 경우가 있어 직계 자식만 보면
    본문을 통째로 놓칠 수 있다. 따라서 깊이 우선으로 하위를 훑되, ``marker``
    컴포넌트를 만나면 그 하위로는 더 내려가지 않아 중첩 컴포넌트 중복을 피하고
    최상위 컴포넌트만 순서대로 돌려준다. ``stop_at_ad_portal``이면 광고 포털
    엘리먼트를 만나는 즉시 멈춘다(본문 끝 표식).
    """
    stopped = False

    def walk(node: Node) -> Iterator[Node]:
        nonlocal stopped
        for child in node.iter(include_text=False):
            if stopped:
                return
            if stop_at_ad_portal and child.attributes.get("id") == AD_PORTAL_ID:
                stopped = True
                return
            if marker in _classes(child):
                yield child
            else:
                yield from walk(child)

    yield from walk(container)


def _classes(node: Node) -> set[str]:
    return set((node.attributes.get("class") or "").split())


def _module_of(component: Node) -> ModuleClass | None:
    """se-component 노드의 모듈 종류를 식별한다."""
    classes = _classes(component)
    for module in ModuleClass:
        if module.value in classes:
            return module
    return None


def _render(module: ModuleClass, component: Node) -> str:
    """모듈 종류에 맞춰 텍스트 블록을 만든다."""
    match module:
        case ModuleClass.TEXT:
            return _render_text(component)
        case ModuleClass.IMAGE | ModuleClass.IMAGE_GROUP:
            return _render_images(component)
        case ModuleClass.OGLINK:
            return _render_oglink(component)
        case ModuleClass.QUOTATION:
            return _render_quotation(component)
        case ModuleClass.VIDEO | ModuleClass.OEMBED:
            return _render_media(component)
        case ModuleClass.CODE | ModuleClass.TABLE:
            return _clean(component.text())
        case ModuleClass.HORIZONTAL_LINE:
            return "─" * 10
        case _:
            return ""


def _render_text(component: Node) -> str:
    """se-text: 문단(.se-text-paragraph)을 줄 단위로 잇는다."""
    lines = [_clean(p.text()) for p in component.css(".se-text-paragraph")]
    return "\n".join(lines).strip("\n")


def _render_images(component: Node) -> str:
    """se-image / se-imageGroup: 이미지 URL과 캡션을 플레이스홀더로 표기한다."""
    parts: list[str] = []
    for img in component.css("img"):
        src = img.attributes.get("data-lazy-src") or img.attributes.get("src") or ""
        src = src.strip()
        if src:
            parts.append(f"[이미지: {src}]")
    # 캡션 클래스는 SE-ONE(하이픈)·구버전(언더스코어) 둘 다 대응한다.
    caption = component.css_first(".se-caption, .se_caption")
    if caption is not None:
        text = _clean(caption.text())
        if text:
            parts.append(text)
    return "\n".join(parts)


def _render_oglink(component: Node) -> str:
    """se-oglink: 링크 카드의 제목과 URL을 표기한다."""
    title_node = component.css_first(".se-oglink-title")
    link = component.css_first("a")
    title = _clean(title_node.text()) if title_node is not None else ""
    href = (link.attributes.get("href") or "").strip() if link is not None else ""
    return _format_link(title, href)


def _format_link(title: str, href: str) -> str:
    """링크 카드를 ``[링크: 제목 URL]`` 플레이스홀더로 표기한다."""
    if title and href:
        return f"[링크: {title} {href}]"
    if href:
        return f"[링크: {href}]"
    return f"[링크: {title}]" if title else ""


def _render_quotation(component: Node) -> str:
    """se-quotation: 인용 블록을 '> '로 들여쓴다."""
    container = component.css_first(".se-quotation-container") or component
    text = _clean(container.text())
    if not text:
        return ""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _render_media(component: Node) -> str:
    """se-video / se-oembed: 가능한 경우 소스 URL을 표기한다."""
    source = component.css_first("iframe, video, source, a")
    if source is not None:
        url = (source.attributes.get("src") or source.attributes.get("href") or "").strip()
        if url:
            return f"[동영상: {url}]"
    return "[동영상]"


def _parse_legacy(container: Node) -> ParsedBody:
    """구버전 SE 3.0 본문(se3_view)을 렌더링한다.

    스마트에디터 ONE과 달리 본문이 여러 ``se_doc_viewer`` 블록에 나뉘어 담기고,
    제목도 ``se_documentTitle`` 컴포넌트로 함께 들어 있다. 제목 컴포넌트는
    걸러내고, 나머지 컴포넌트는 종류에 맞춰 렌더링한다. 알 수 없는 컴포넌트는
    텍스트라도 보존해 본문 누락을 막는다.
    """
    blocks: list[str] = []
    has_content = False
    for component in _iter_legacy_components(container):
        classes = _classes(component)
        # 제목 컴포넌트는 본문이 아니므로 종류 판별보다 먼저 걸러낸다.
        if LegacyModuleClass.DOCUMENT_TITLE in classes:
            continue
        # 빈 글 판정은 SE-ONE과 같은 기준(콘텐츠 모듈 집합)으로 한다.
        if classes & LEGACY_CONTENT_MODULE_CLASSES:
            has_content = True
        rendered = _render_legacy(classes, component)
        if rendered:
            blocks.append(rendered)

    return ParsedBody(text=_finalize("\n\n".join(blocks)), has_content=has_content)


def _iter_legacy_components(container: Node) -> Iterator[Node]:
    """se3_view의 se_component를 문서 순서대로 순회한다(광고 포털 표식 없음)."""
    return _iter_components_by_marker(container, _LEGACY_COMPONENT_CLASS, stop_at_ad_portal=False)


def _render_legacy(classes: set[str], component: Node) -> str:
    """구버전 컴포넌트를 클래스에 맞춰 텍스트 블록으로 만든다.

    제목(``se_documentTitle``)은 호출 전에 이미 걸러진다. 종류를 모르는
    컴포넌트는 텍스트라도 보존하되, 빈 글 판정에는 반영하지 않는다.
    """
    if LegacyModuleClass.TEXT in classes:
        return _render_legacy_text(component)
    if LegacyModuleClass.OGLINK in classes:
        return _render_legacy_oglink(component)
    if LegacyModuleClass.IMAGE in classes:
        return _render_images(component)
    if LegacyModuleClass.QUOTATION in classes:
        return _render_quotation(component)
    # 종류를 모르는 컴포넌트라도 텍스트가 있으면 보존한다.
    return _clean(component.text())


def _render_legacy_text(component: Node) -> str:
    """se_paragraph: se_textarea의 텍스트를 <br> 기준 줄바꿈으로 잇는다."""
    lines: list[str] = []
    for area in component.css(".se_textarea"):
        html = _BR_TAG.sub("\n", area.html or "")
        text = _clean(HTMLParser(html).text())
        if text:
            lines.append(text)
    return "\n".join(lines).strip("\n")


def _render_legacy_oglink(component: Node) -> str:
    """se_oglink: 링크 카드의 제목과 URL을 표기한다."""
    title_node = component.css_first(".se_og_tit")
    link = component.css_first("a.__se_link") or component.css_first("a")
    title = _clean(title_node.text()) if title_node is not None else ""
    href = (link.attributes.get("href") or "").strip() if link is not None else ""
    return _format_link(title, href)


def _clean(text: str) -> str:
    """제로 폭 공백·nbsp·잉여 공백을 정리한다."""
    text = _ZERO_WIDTH.sub("", text)
    text = text.replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _finalize(text: str) -> str:
    """본문 전체의 줄 끝 공백과 과도한 빈 줄을 정리한다."""
    text = _TRAILING_WS.sub("\n", text)
    text = _MANY_BLANK_LINES.sub("\n\n", text)
    return text.strip()
