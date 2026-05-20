"""파일명 정제·경로·문서 렌더링·재개 탐지 테스트."""

from __future__ import annotations

from pathlib import Path

from naver_blog_crawler.models import Post, PostMeta
from naver_blog_crawler.writer import (
    find_existing,
    render_document,
    sanitize_title,
    target_path,
    write_post,
)

# 2023-08-21 09:00 KST = 1692576000000 ms
_META = PostMeta(log_no=123, title="제목/테스트", add_date_ms=1692576000000, is_anniversary=False)


def test_sanitize_replaces_illegal_chars() -> None:
    assert "/" not in sanitize_title('a/b:c*d?e"f')


def test_sanitize_collapses_whitespace_and_strips_dot() -> None:
    assert sanitize_title("  여러   공백  .") == "여러 공백"


def test_sanitize_truncates_long_title_by_bytes() -> None:
    result = sanitize_title("가" * 200)
    # 바이트 예산(200) 이하이며, 글자 경계가 깨지지 않아야 한다.
    assert len(result.encode("utf-8")) <= 200
    assert "가" * len(result) == result


def test_sanitize_empty_falls_back() -> None:
    assert sanitize_title("///") == "무제"


def test_target_path_format() -> None:
    path = target_path(Path("/out"), 7, _META)
    assert path.name == "0007_2023-08-21_제목 테스트.txt"


def test_render_document_has_header_and_body() -> None:
    post = Post(meta=_META, url="https://m.blog.naver.com/x/123", body="본문 내용")
    doc = render_document(post)
    assert doc.startswith("제목: 제목/테스트\n")
    assert "날짜: 2023-08-21" in doc
    assert "본문 내용" in doc


def test_write_and_find_existing_roundtrip(tmp_path: Path) -> None:
    post = Post(meta=_META, url="https://m.blog.naver.com/x/123", body="본문")
    written = write_post(tmp_path, 3, post)
    assert written.exists()
    assert find_existing(tmp_path, 3) == written
    assert find_existing(tmp_path, 4) is None


def test_write_removes_stale_same_seq_file(tmp_path: Path) -> None:
    # 같은 순번의 옛 파일(제목이 바뀌기 전)을 미리 만들어 둔다.
    stale = tmp_path / "0003_2023-08-21_옛 제목.txt"
    stale.write_text("옛 내용", encoding="utf-8")

    post = Post(meta=_META, url="https://m.blog.naver.com/x/123", body="본문")
    written = write_post(tmp_path, 3, post)

    assert written.exists()
    assert not stale.exists()
    # 순번 3에 대한 파일은 하나만 남아야 한다.
    assert len(list(tmp_path.glob("0003_*.txt"))) == 1
