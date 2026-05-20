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
    MAIN_CONTAINER_SELECTOR,
    ModuleClass,
)
from .errors import ParseError

# 네이버가 빈 줄·공백에 쓰는 제로 폭 공백과 줄 끝 정리용 정규식.
_ZERO_WIDTH = re.compile(r"[​﻿]")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class ParsedBody:
    """렌더링된 본문과 콘텐츠 보유 여부."""

    text: str
    has_content: bool


def parse_post_body(html: str) -> ParsedBody:
    """PostView HTML에서 본문 텍스트를 추출한다.

    Args:
        html: PostView 페이지 전체 HTML.

    Returns:
        렌더링된 본문 텍스트와, 실제 사용자 콘텐츠 모듈이 하나라도 있는지 여부.

    Raises:
        ParseError: ``se-main-container``를 찾지 못한 경우.
    """
    tree = HTMLParser(html)
    container = tree.css_first(MAIN_CONTAINER_SELECTOR)
    if container is None:
        raise ParseError("se-main-container를 찾을 수 없습니다.")

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
    """본문 컨테이너의 se-component를 문서 순서대로, 광고 포털 직전까지 순회한다.

    네이버가 컴포넌트를 레이아웃 div로 감싸는 경우가 있어 직계 자식만 보면
    본문을 통째로 놓칠 수 있다. 따라서 깊이 우선으로 하위를 훑되, 일단
    se-component를 만나면 그 안으로는 더 내려가지 않아(중첩 컴포넌트 중복 방지)
    최상위 컴포넌트만 순서대로 돌려준다. 광고 포털을 만나면 즉시 멈춘다.
    """
    stopped = False

    def walk(node: Node) -> Iterator[Node]:
        nonlocal stopped
        for child in node.iter(include_text=False):
            if stopped:
                return
            if child.attributes.get("id") == AD_PORTAL_ID:
                stopped = True
                return
            if "se-component" in _classes(child):
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
    caption = component.css_first(".se-caption")
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
