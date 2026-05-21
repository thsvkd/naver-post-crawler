"""CLI 재시도 결정 로직 및 중단 처리 테스트."""

from __future__ import annotations

from collections.abc import Iterator

from naver_blog_crawler.cli import _decide_retry, _run
from naver_blog_crawler.crawler import Outcome, PostResult
from naver_blog_crawler.models import PostMeta


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


class _Plan:
    total = 5


class _InterruptingCrawler:
    """두 건을 처리한 뒤 Ctrl-C가 들어온 상황을 흉내낸다."""

    def run(self, plan: object) -> Iterator[PostResult]:
        meta = PostMeta(log_no=1, title="글", add_date_ms=1692576000000, is_anniversary=False)
        yield PostResult(1, 5, meta, Outcome.WRITTEN)
        yield PostResult(2, 5, meta, Outcome.WRITTEN)
        raise KeyboardInterrupt


def test_run_handles_interrupt_and_keeps_partial_results() -> None:
    counts, failed, interrupted = _run(_InterruptingCrawler(), _Plan())
    assert interrupted is True
    assert counts[Outcome.WRITTEN] == 2  # 중단 전까지의 결과는 보존
    assert failed == []
