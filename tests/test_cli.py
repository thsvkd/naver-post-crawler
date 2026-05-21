"""CLI 재시도 결정 로직 테스트."""

from __future__ import annotations

from naver_blog_crawler.cli import _decide_retry


def test_decide_retry_force_is_false() -> None:
    assert _decide_retry(None, 3, force=True) is False


def test_decide_retry_no_pending_is_false() -> None:
    assert _decide_retry(True, 0, force=False) is False


def test_decide_retry_flag_takes_priority() -> None:
    assert _decide_retry(True, 3, force=False) is True
    assert _decide_retry(False, 3, force=False) is False


def test_decide_retry_non_interactive_defaults_false() -> None:
    # pytest는 비대화형(콘솔 비-TTY)이므로 플래그 미지정 시 건너뛴다.
    assert _decide_retry(None, 3, force=False) is False
