"""CLI 재시도 결정 로직·중단 처리·쿠키 출처 우선순위 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import click
import pytest

import naver_post_crawler.cli as cli_mod
from naver_post_crawler.cli import _decide_retry, _resolve_cli_cookie, _run
from naver_post_crawler.crawler import Outcome, PostResult
from naver_post_crawler.models import PostMeta


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


def test_resolve_cli_cookie_prefers_string(monkeypatch: pytest.MonkeyPatch) -> None:
    # 문자열 쿠키가 있으면 파일·저장된 쿠키를 보지 않는다.
    monkeypatch.setattr(cli_mod, "load_cookie", lambda: "STORED")
    assert _resolve_cli_cookie("NID_SES=x", Path("ignored.txt")) == "NID_SES=x"


def test_resolve_cli_cookie_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(".naver.com\tTRUE\t/\tTRUE\t0\tNID_SES\tFROMFILE\n", encoding="utf-8")
    assert _resolve_cli_cookie(None, path) == "NID_SES=FROMFILE"


def test_resolve_cli_cookie_bad_file_raises_bad_parameter(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text(".google.com\tTRUE\t/\tFALSE\t0\tX\tY\n", encoding="utf-8")
    with pytest.raises(click.BadParameter):
        _resolve_cli_cookie(None, path)


def test_resolve_cli_cookie_falls_back_to_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "load_cookie", lambda: "STORED")
    assert _resolve_cli_cookie(None, None) == "STORED"
