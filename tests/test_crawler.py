"""크롤러 보조 로직(_realign) 테스트."""

from __future__ import annotations

from pathlib import Path

from naver_blog_crawler.crawler import _realign


def test_realign_renames_when_seq_drifts(tmp_path: Path) -> None:
    existing = tmp_path / "0500_2023-08-21_제목_123.txt"
    existing.write_text("내용", encoding="utf-8")
    desired = tmp_path / "0499_2023-08-21_제목_123.txt"

    result = _realign(existing, desired)

    assert result == desired
    assert desired.exists()
    assert not existing.exists()


def test_realign_keeps_path_when_already_correct(tmp_path: Path) -> None:
    existing = tmp_path / "0001_2023-08-21_제목_123.txt"
    existing.write_text("내용", encoding="utf-8")

    assert _realign(existing, existing) == existing
    assert existing.exists()


def test_realign_avoids_clobbering_occupied_target(tmp_path: Path) -> None:
    existing = tmp_path / "0500_2023-08-21_제목_123.txt"
    existing.write_text("내용", encoding="utf-8")
    occupied = tmp_path / "0499_2023-08-21_다른글_456.txt"
    occupied.write_text("다른 내용", encoding="utf-8")

    # 목표 이름이 다른 글에 점유돼 있으면 옮기지 않고 기존 경로를 유지한다.
    result = _realign(existing, occupied)

    assert result == existing
    assert existing.exists()
    assert occupied.read_text(encoding="utf-8") == "다른 내용"
