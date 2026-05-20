"""명령줄 인터페이스 (click + rich)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .client import NaverBlogClient
from .crawler import Crawler, Outcome, PostResult

console = Console()

_OUTCOME_STYLE: dict[Outcome, tuple[str, str]] = {
    Outcome.WRITTEN: ("저장", "green"),
    Outcome.SKIPPED_EXISTING: ("건너뜀(기존)", "cyan"),
    Outcome.SKIPPED_EMPTY: ("건너뜀(빈 글)", "yellow"),
    Outcome.FAILED: ("실패", "red"),
}


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("blog_id")
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
    "--delay",
    type=float,
    default=0.5,
    show_default=True,
    help="요청 사이 대기 시간(초).",
)
@click.option(
    "--max-retries",
    type=int,
    default=3,
    show_default=True,
    help="요청 실패 시 최대 재시도 횟수.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="처리할 글 수 제한(과거부터). 미지정 시 전체.",
)
@click.option(
    "--force",
    is_flag=True,
    help="이미 저장된 글도 다시 받아 덮어쓴다.",
)
def main(
    blog_id: str,
    out_dir: Path,
    delay: float,
    max_retries: int,
    limit: int | None,
    force: bool,
) -> None:
    """네이버 블로그 BLOG_ID의 전체 글을 과거→최근 순으로 txt로 백업한다."""
    console.print(f"[bold]네이버 블로그 백업[/bold] · blog_id=[cyan]{blog_id}[/cyan]")

    with NaverBlogClient(blog_id, delay=delay, max_retries=max_retries) as client:
        crawler = Crawler(client, out_dir, force=force)

        with console.status("[bold]글 목록 수집 중…[/bold]"):
            plan = crawler.build_plan()

        console.print(
            f"전체 글 [bold]{plan.total + plan.skipped_anniversary}[/bold]건 중 "
            f"대상 [bold green]{plan.total}[/bold green]건, "
            f"'그날의 추억' 제외 [yellow]{plan.skipped_anniversary}[/yellow]건"
        )

        if limit is not None:
            plan.targets[limit:] = []
            console.print(f"[dim]--limit 적용: 과거부터 {plan.total}건만 처리[/dim]")

        counts: Counter[Outcome] = Counter()
        failures: list[PostResult] = []

        with _make_progress() as progress:
            task = progress.add_task("백업", total=plan.total)
            for result in crawler.run(plan):
                counts[result.outcome] += 1
                if result.outcome is Outcome.FAILED:
                    failures.append(result)
                progress.update(
                    task,
                    advance=1,
                    description=_describe(result),
                )

    _print_summary(counts, out_dir)
    _print_failures(failures)


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )


def _describe(result: PostResult) -> str:
    label, style = _OUTCOME_STYLE[result.outcome]
    title = result.meta.title[:30]
    return f"[{style}]{label}[/{style}] {result.seq:04d} {title}"


def _print_summary(counts: Counter[Outcome], out_dir: Path) -> None:
    table = Table(title="백업 결과", show_header=True, header_style="bold")
    table.add_column("구분")
    table.add_column("건수", justify="right")
    for outcome in Outcome:
        label, style = _OUTCOME_STYLE[outcome]
        table.add_row(f"[{style}]{label}[/{style}]", str(counts.get(outcome, 0)))
    console.print(table)
    console.print(f"저장 위치: [bold]{out_dir.resolve()}[/bold]")


def _print_failures(failures: list[PostResult]) -> None:
    if not failures:
        return
    console.print("\n[bold red]실패한 글[/bold red]")
    for result in failures:
        console.print(f"  - {result.seq:04d} {result.meta.title[:40]} · {result.error}")


if __name__ == "__main__":
    main()
