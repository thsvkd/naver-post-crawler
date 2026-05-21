"""FailureStore 저장/로드 및 saved_log_nos 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from naver_blog_crawler.errors import CrawlerError
from naver_blog_crawler.failures import FailureStore
from naver_blog_crawler.models import PostMeta
from naver_blog_crawler.writer import saved_log_nos

_META = PostMeta(log_no=123, title="실패한 글", add_date_ms=1692576000000, is_anniversary=False)


def test_record_save_load_roundtrip(tmp_path: Path) -> None:
    store = FailureStore.load(tmp_path)
    store.record(_META, "se-main-container를 찾을 수 없습니다.")
    store.save()

    reloaded = FailureStore.load(tmp_path)
    assert 123 in reloaded
    assert len(reloaded) == 1
    assert reloaded.records[0].title == "실패한 글"
    assert reloaded.records[0].attempts == 1


def test_repeated_record_accumulates_attempts(tmp_path: Path) -> None:
    store = FailureStore.load(tmp_path)
    store.record(_META, "오류1")
    store.record(_META, "오류2")
    assert store.records[0].attempts == 2
    assert store.records[0].error == "오류2"


def test_clear_then_save_removes_file(tmp_path: Path) -> None:
    store = FailureStore.load(tmp_path)
    store.record(_META, "오류")
    store.save()
    assert (tmp_path / ".failures.json").exists()

    store.clear(123)
    store.save()
    # 비면 사이드카 파일을 남기지 않는다.
    assert not (tmp_path / ".failures.json").exists()


def test_load_corrupt_file_raises(tmp_path: Path) -> None:
    (tmp_path / ".failures.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CrawlerError):
        FailureStore.load(tmp_path)


def test_saved_log_nos_extracts_trailing_id(tmp_path: Path) -> None:
    (tmp_path / "0001_2023-08-21_제목_223189475037.txt").write_text("x", encoding="utf-8")
    (tmp_path / "0002_2023-08-22_다른 글_223189644114.txt").write_text("x", encoding="utf-8")
    (tmp_path / "README.txt").write_text("x", encoding="utf-8")  # logNo 없음 → 무시
    assert saved_log_nos(tmp_path) == {223189475037, 223189644114}


def test_saved_log_nos_missing_dir(tmp_path: Path) -> None:
    assert saved_log_nos(tmp_path / "nope") == set()
