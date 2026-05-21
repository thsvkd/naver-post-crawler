"""명령줄 인터페이스 (click + rich).

실행하면 대상 글을 요약하고, 이전에 실패한 글이 있으면 재시도 여부를 대화형으로
물은 뒤, 진행바와 최근 결과 로그가 함께 갱신되는 Live 화면으로 백업을 진행한다.
"""

from __future__ import annotations

import logging
from collections import Counter, deque
from pathlib import Path

import click
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from .blog_id import resolve_blog_id
from .client import NaverBlogClient
from .crawler import Crawler, CrawlPlan, Outcome, PostResult
from .errors import InvalidBlogReference
from .failures import FailureStore
from .log import setup_logging
from .writer import saved_log_nos

console = Console()
logger = logging.getLogger(__name__)

# 결과 종류별 (라벨, 색, 아이콘).
_OUTCOME_STYLE: dict[Outcome, tuple[str, str, str]] = {
    Outcome.WRITTEN: ("저장", "green", "✓"),
    Outcome.SKIPPED_EXISTING: ("건너뜀(기존)", "cyan", "•"),
    Outcome.SKIPPED_EMPTY: ("건너뜀(빈 글)", "yellow", "•"),
    Outcome.SKIPPED_FAILED: ("건너뜀(이전 실패)", "magenta", "•"),
    Outcome.FAILED: ("실패", "red", "✗"),
}
# Live 화면 하단에 보여줄 최근 결과 줄 수(고정 높이로 유지해 깜빡임을 줄인다).
_RECENT_LINES = 8
# rich Live는 매 refresh마다 영역 전체를 지우고 다시 그리므로, 화면 영역이
# 클수록·빠를수록 깜빡인다. 작은 영역에서 부드럽게 도는 Progress 기본값과
# 같은 수준으로 맞춘다.
_REFRESH_PER_SECOND = 8


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("blog")
@click.option(
    "-o",
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("output"),
    show_default=True,
    help="txt 파일을 저장할 디렉토리.",
)
@click.option(
    "--delay", type=float, default=0.5, show_default=True, help="요청 사이 대기 시간(초)."
)
@click.option(
    "--max-retries", type=int, default=3, show_default=True, help="요청 실패 시 최대 재시도 횟수."
)
@click.option(
    "--limit", type=int, default=None, help="처리할 글 수 제한(과거부터). 미지정 시 전체."
)
@click.option(
    "--retry-failed/--no-retry-failed",
    "retry_flag",
    default=None,
    help="이전에 실패한 글 재시도 여부. 미지정 시 대화형으로 묻는다.",
)
@click.option("--force", is_flag=True, help="이미 저장된 글도 다시 받아 덮어쓴다.")
@click.option(
    "--log-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("logs"),
    show_default=True,
    help="로그 파일을 저장할 디렉토리.",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default="INFO",
    show_default=True,
    help="파일 로그 레벨.",
)
def main(
    blog: str,
    out_dir: Path,
    delay: float,
    max_retries: int,
    limit: int | None,
    retry_flag: bool | None,
    force: bool,
    log_dir: Path,
    log_level: str,
) -> None:
    """네이버 블로그의 전체 글을 과거→최근 순으로 txt로 백업한다.

    BLOG 는 블로그 아이디(winter9377)나 블로그·포스트 URL 모두 가능하다.
    """
    log_file = setup_logging(log_dir, level=logging.getLevelNamesMapping()[log_level.upper()])

    try:
        blog_id = resolve_blog_id(blog)
    except InvalidBlogReference as exc:
        raise click.BadParameter(str(exc), param_hint="BLOG") from exc

    logger.info("백업 시작: blog_id=%s out=%s force=%s", blog_id, out_dir, force)
    console.print(f"[bold]네이버 블로그 백업[/bold] · blog_id=[cyan]{blog_id}[/cyan]")
    console.print(f"[dim]로그: {log_file}[/dim]")

    with NaverBlogClient(blog_id, delay=delay, max_retries=max_retries) as client:
        failures = FailureStore.load(out_dir)

        with console.status("[bold]글 목록 수집 중…[/bold]"):
            crawler = Crawler(client, out_dir, failures, force=force)
            plan = crawler.build_plan()

        if limit is not None:
            plan.targets[limit:] = []

        pending_failed = _print_plan(plan, out_dir, failures, force=force)
        crawler.retry_failed = _decide_retry(retry_flag, pending_failed, force=force)

        # 중단(Ctrl-C) 시에도 진행분 실패 기록을 반드시 저장한다.
        try:
            counts, failed_results, interrupted = _run(crawler, plan)
        finally:
            failures.save()

    if interrupted:
        logger.warning("사용자 중단(KeyboardInterrupt) — 진행분까지 저장")
        console.print(
            "[yellow]중단됨[/yellow] — 지금까지 받은 글은 저장되었습니다. "
            "다시 실행하면 이어서 진행합니다."
        )

    logger.info(
        "백업 종료: 저장 %d, 기존 %d, 빈 글 %d, 이전 실패 %d, 실패 %d",
        counts.get(Outcome.WRITTEN, 0),
        counts.get(Outcome.SKIPPED_EXISTING, 0),
        counts.get(Outcome.SKIPPED_EMPTY, 0),
        counts.get(Outcome.SKIPPED_FAILED, 0),
        counts.get(Outcome.FAILED, 0),
    )
    _print_summary(counts, out_dir)
    _print_failures(failed_results)


def _print_plan(plan: CrawlPlan, out_dir: Path, failures: FailureStore, *, force: bool) -> int:
    """대상 글을 분류해 요약을 출력하고, 다시 시도할 이전 실패 건수를 돌려준다."""
    saved = set() if force else saved_log_nos(out_dir)
    already = sum(1 for m in plan.targets if m.log_no in saved)
    pending_failed = sum(1 for m in plan.targets if m.log_no not in saved and m.log_no in failures)
    new_posts = plan.total - already - pending_failed

    console.print(
        f"전체 글 [bold]{plan.total + plan.skipped_anniversary}[/bold]건 중 "
        f"대상 [bold]{plan.total}[/bold]건"
        f" · '그날의 추억' 제외 [yellow]{plan.skipped_anniversary}[/yellow]건"
    )
    console.print(
        f"  새 글 [green]{new_posts}[/green] · "
        f"이미 저장 [cyan]{already}[/cyan] · "
        f"이전 실패 [magenta]{pending_failed}[/magenta]"
    )
    return pending_failed


def _decide_retry(retry_flag: bool | None, pending_failed: int, *, force: bool) -> bool:
    """이전 실패 글을 다시 시도할지 결정한다(플래그 > 대화형 > 비대화형 기본값)."""
    if force or pending_failed == 0:
        return False
    if retry_flag is not None:
        return retry_flag
    if not console.is_terminal:
        console.print("[dim]비대화형 환경: 이전 실패 글은 건너뜁니다(--retry-failed로 강제).[/dim]")
        return False
    return Confirm.ask(
        f"이전에 실패한 [magenta]{pending_failed}[/magenta]건을 다시 시도할까요?",
        default=True,
    )


def _run(crawler: Crawler, plan: CrawlPlan) -> tuple[Counter[Outcome], list[PostResult], bool]:
    """진행바와 최근 결과 로그가 함께 갱신되는 Live 화면으로 백업을 진행한다.

    Ctrl-C로 중단하면 거기까지의 결과를 반환하고 중단 여부를 함께 알린다.
    """
    counts: Counter[Outcome] = Counter()
    failed_results: list[PostResult] = []
    recent: deque[Text] = deque(maxlen=_RECENT_LINES)
    interrupted = False

    progress = _make_progress()
    task = progress.add_task("백업 진행", total=plan.total)

    def render() -> Group:
        # 항상 _RECENT_LINES 줄로 채워 영역 높이를 고정한다(높이 변동 잔상 방지).
        blank = Text("", style="dim")
        lines = [blank] * (_RECENT_LINES - len(recent)) + list(recent)
        log = Text("\n").join(lines)
        return Group(progress, Panel(log, title="최근 결과", border_style="dim"))

    with Live(
        render(),
        console=console,
        refresh_per_second=_REFRESH_PER_SECOND,
        vertical_overflow="crop",
    ) as live:
        try:
            for result in crawler.run(plan):
                counts[result.outcome] += 1
                if result.outcome is Outcome.FAILED:
                    failed_results.append(result)
                progress.advance(task)
                recent.append(_recent_line(result))
                # refresh는 타이머에 맡긴다(여기서 강제하면 프레임이 겹쳐 깜빡인다).
                live.update(render())
        except KeyboardInterrupt:
            interrupted = True

    return counts, failed_results, interrupted


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )


def _recent_line(result: PostResult) -> Text:
    label, style, icon = _OUTCOME_STYLE[result.outcome]
    line = Text()
    line.append(f"{icon} ", style=style)
    line.append(f"{result.seq:04d} ", style="dim")
    line.append(result.meta.title[:42])
    if result.error:
        line.append(f"  · {result.error}", style="red")
    return line


def _print_summary(counts: Counter[Outcome], out_dir: Path) -> None:
    table = Table(title="백업 결과", show_header=True, header_style="bold")
    table.add_column("구분")
    table.add_column("건수", justify="right")
    for outcome in Outcome:
        label, style, _icon = _OUTCOME_STYLE[outcome]
        table.add_row(f"[{style}]{label}[/{style}]", str(counts.get(outcome, 0)))
    console.print(table)
    console.print(f"저장 위치: [bold]{out_dir.resolve()}[/bold]")


def _print_failures(failures: list[PostResult]) -> None:
    if not failures:
        return
    console.print(
        "\n[bold red]실패한 글[/bold red] [dim](다음 실행 시 재시도 여부를 묻습니다)[/dim]"
    )
    for result in failures:
        console.print(f"  - {result.seq:04d} {result.meta.title[:40]} · {result.error}")


if __name__ == "__main__":
    main()
