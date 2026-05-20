"""추출한 글을 txt 파일로 저장한다.

글 1개 → 파일 1개. 파일명은 ``0001_<날짜>_<제목>.txt`` 형식이며 점두 번호로
과거→최근 순서를 보존한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Post, PostMeta

# 파일명에 쓸 수 없는 문자(윈도/리눅스 공통)와 제어 문자.
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")

# 대부분의 파일시스템은 파일명을 255바이트로 제한한다. 한글은 UTF-8에서
# 글자당 3바이트라 글자 수로 자르면 한계를 넘을 수 있으므로 바이트로 자른다.
# 번호·날짜 접두사와 확장자 몫을 빼고 제목에 허용할 바이트 예산.
_MAX_TITLE_BYTES = 200


def sanitize_title(title: str) -> str:
    """제목을 파일명에 안전한 형태로 정제한다."""
    cleaned = _ILLEGAL_CHARS.sub(" ", title)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    # 끝의 마침표·공백은 윈도에서 문제가 되므로 제거한다.
    cleaned = cleaned.rstrip(". ")
    cleaned = _truncate_bytes(cleaned, _MAX_TITLE_BYTES)
    return cleaned or "무제"


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """UTF-8 바이트 길이가 ``max_bytes`` 이하가 되도록, 글자 경계에서 자른다."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # 잘린 바이트열의 불완전한 마지막 글자는 버린다.
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def target_path(out_dir: Path, seq: int, meta: PostMeta) -> Path:
    """글 한 건이 저장될 경로를 만든다."""
    name = f"{seq:04d}_{meta.date_str}_{sanitize_title(meta.title)}.txt"
    return out_dir / name


def find_existing(out_dir: Path, seq: int) -> Path | None:
    """해당 순번으로 이미 저장된 파일이 있으면 반환한다(재개용)."""
    matches = sorted(out_dir.glob(f"{seq:04d}_*.txt"))
    return matches[0] if matches else None


def render_document(post: Post) -> str:
    """파일에 기록할 전체 문서 문자열을 만든다."""
    header = f"제목: {post.meta.title}\n날짜: {post.meta.date_str}\n주소: {post.url}\n"
    separator = "=" * 60
    return f"{header}{separator}\n\n{post.body}\n"


def write_post(out_dir: Path, seq: int, post: Post) -> Path:
    """글 한 건을 파일로 저장하고 경로를 반환한다.

    제목이 바뀌어 파일명이 달라진 경우 같은 순번의 옛 파일이 남아 중복되지
    않도록, 대상과 다른 ``{seq}_*`` 파일은 지운 뒤 기록한다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = target_path(out_dir, seq, post.meta)
    for stale in out_dir.glob(f"{seq:04d}_*.txt"):
        if stale != path:
            stale.unlink()
    path.write_text(render_document(post), encoding="utf-8")
    return path
