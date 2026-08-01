"""CLI 재시도 결정 로직·중단 처리·쿠키 출처 우선순위 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import naver_post_crawler.cli as cli_mod
from naver_post_crawler.cli import _decide_retry, _resolve_cli_cookie, _run, main
from naver_post_crawler.cookie import CookieMigration, MigrationResult
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


def test_since_after_until_raises_usage_error() -> None:
    """--since가 --until보다 늦으면 UsageError로 즉시 중단한다."""
    runner = CliRunner()
    result = runner.invoke(main, ["--since", "2023-12-31", "--until", "2023-01-01", "target"])
    assert result.exit_code != 0
    assert "--since가 --until보다" in result.output


# -- 평문 쿠키 이관 배선 ------------------------------------------------------
# 이 변경의 존재 이유는 평문 파일을 없애는 것이다. 결과를 받은 뒤의 분기만 검증하고
# **호출 자체**를 검증하지 않으면, 이관을 통째로 지워도 스위트가 초록색으로 남는다.
# covers 태그의 번호는 docs/handoff-credential-storage.md의 인수 기준이다.


def _spy_migration(
    monkeypatch: pytest.MonkeyPatch,
    outcome: CookieMigration = CookieMigration.NOTHING,
    *,
    exposed: bool = False,
) -> list[int]:
    calls: list[int] = []
    result = MigrationResult(outcome, exposed=exposed, path=Path("/tmp/cafe_cookie.txt"))

    def fake() -> MigrationResult:
        calls.append(1)
        return result

    monkeypatch.setattr(cli_mod, "migrate_legacy_cookie", fake)
    monkeypatch.setattr(cli_mod, "_check_update", lambda: None)
    return calls


def test_cli_runs_the_legacy_cookie_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: cred/Test-8
    calls = _spy_migration(monkeypatch)

    result = CliRunner().invoke(main, ["--check-update"])

    assert result.exit_code == 0, result.output
    assert calls == [1], "CLI 시작 시 이관을 정확히 한 번 실행해야 한다"


def test_cli_reports_a_lost_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: cred/Test-12b
    _spy_migration(monkeypatch, CookieMigration.LOST)

    result = CliRunner().invoke(main, ["--check-update"])

    # rich가 폭에 맞춰 줄을 접으므로 공백을 지우고 비교한다.
    assert "네이버로그인" in "".join(result.output.split()), (
        "쿠키를 잃었으면 무엇을 해야 하는지 알려야 한다"
    )


def test_cli_reports_a_leftover_plaintext_file(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: cred/Test-12b (지우지 못했으면 평문이 그대로 남아 있다)
    _spy_migration(monkeypatch, CookieMigration.MOVED, exposed=True)

    result = CliRunner().invoke(main, ["--check-update"])

    compact = "".join(result.output.split())
    assert "직접삭제해주세요" in compact
    assert "cafe_cookie.txt" in compact, "어느 파일을 지워야 하는지 알려야 한다"
