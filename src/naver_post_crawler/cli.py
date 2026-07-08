"""명령줄 인터페이스 (click + rich).

실행하면 대상 글을 요약하고, 이전에 실패한 글이 있으면 재시도 여부를 대화형으로
물은 뒤, 진행바와 최근 결과 로그가 함께 갱신되는 Live 화면으로 백업을 진행한다.
"""

from __future__ import annotations

import logging
from collections import Counter, deque
from datetime import date, datetime
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

from . import __version__, updater
from .blog_id import resolve_blog_id
from .cafe_client import NaverCafeClient
from .cafe_ref import is_cafe_reference, resolve_cafe_reference
from .client import NaverBlogClient
from .cookie import load_cookie, parse_cookie_file
from .crawler import Crawler, CrawlPlan, Outcome, PostResult
from .errors import (
    BlogNotFound,
    CafeNotFound,
    InvalidBlogReference,
    InvalidCafeReference,
    InvalidCookieFile,
    LoginRequired,
)
from .failures import FailureStore
from .log import setup_logging
from .parser import parse_cafe_body
from .source import PostSource
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
@click.version_option(__version__, "-V", "--version", prog_name="naver-post-crawler")
@click.argument("target", required=False)
@click.option("--check-update", is_flag=True, help="최신 릴리스가 있는지 확인만 하고 종료한다.")
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
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="이 날짜(YYYY-MM-DD, KST 기준) 이후 글만 받는다(경계 포함).",
)
@click.option(
    "--until",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="이 날짜(YYYY-MM-DD, KST 기준) 이전 글만 받는다(경계 포함).",
)
@click.option(
    "--retry-failed/--no-retry-failed",
    "retry_flag",
    default=None,
    help="이전에 실패한 글 재시도 여부. 미지정 시 대화형으로 묻는다.",
)
@click.option("--force", is_flag=True, help="이미 저장된 글도 다시 받아 덮어쓴다.")
@click.option(
    "--cookie",
    default=None,
    help="[카페] 세션 쿠키 문자열 'NID_AUT=...; NID_SES=...'. 로그인/등급 제한 게시판 접근에 필요.",
)
@click.option(
    "--cookie-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="[카페] 브라우저 확장으로 내보낸 쿠키 파일(cookies.txt/JSON) 경로. --cookie 대신 쓴다.",
)
@click.option(
    "--menu",
    "menu_id",
    type=int,
    default=None,
    help="[카페] 특정 게시판만 받을 때의 menuId. 미지정 시 접근 가능한 게시판 전체.",
)
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
    target: str | None,
    check_update: bool,
    out_dir: Path,
    delay: float,
    max_retries: int,
    limit: int | None,
    since: datetime | None,
    until: datetime | None,
    retry_flag: bool | None,
    force: bool,
    cookie: str | None,
    cookie_file: Path | None,
    menu_id: int | None,
    log_dir: Path,
    log_level: str,
) -> None:
    """네이버 블로그·카페의 글을 과거→최근 순으로 txt로 백업한다.

    TARGET 은 블로그 아이디(winter9377)·블로그/포스트 URL이거나, 네이버 카페
    주소(cafe.naver.com/...)다. 카페 주소면 카페 모드로 동작하며, 로그인/등급
    제한 게시판은 --cookie(문자열)나 --cookie-file(파일)로 세션 쿠키를 주입해야 한다.
    """
    # --check-update: 최신 릴리스만 확인하고 종료한다(TARGET 불필요). 네트워크 오류는
    # 트레이스백 대신 경고로 삼켜 크롤링 없이 조용히 끝낸다.
    if check_update:
        try:
            release = updater.check_latest(__version__, updater.current_target())
        except Exception as exc:
            # em-dash(U+2014)는 cp949 콘솔에서 인코딩되지 않아, 리다이렉트 시 출력이
            # UnicodeEncodeError로 죽는다. 네트워크 실패를 조용히 알리려면 ASCII 구분자를 쓴다.
            console.print(f"[yellow]업데이트 확인 실패[/yellow]: {exc}")
            return
        if release is None:
            console.print(f"현재 최신 버전입니다 (v{__version__}).")
        else:
            console.print(
                f"새 버전 v{release.version} 사용 가능 (현재 v{__version__}). "
                f"릴리스: {release.asset_url}"
            )
        return
    if not target:
        raise click.UsageError("TARGET을 지정하세요 (또는 --check-update / --version).")

    # 진행 화면(Live)과 같은 콘솔을 넘겨, 로그가 진행바 위로 흐르도록 한다.
    log_file = setup_logging(
        log_dir,
        level=logging.getLevelNamesMapping()[log_level.upper()],
        console=console,
    )

    if is_cafe_reference(target):
        title, client, crawler, failures = _build_cafe(
            target,
            out_dir,
            delay=delay,
            max_retries=max_retries,
            cookie=_resolve_cli_cookie(cookie, cookie_file),
            menu_id=menu_id,
        )
    else:
        title, client, crawler, failures = _build_blog(
            target, out_dir, delay=delay, max_retries=max_retries
        )
    crawler.force = force

    console.print(title)
    console.print(f"[dim]로그: {log_file}[/dim]")
    since_date = since.date() if since is not None else None
    until_date = until.date() if until is not None else None
    _backup(
        client,
        crawler,
        out_dir,
        failures,
        limit=limit,
        since=since_date,
        until=until_date,
        retry_flag=retry_flag,
        force=force,
    )


def _build_blog(
    target: str, out_dir: Path, *, delay: float, max_retries: int
) -> tuple[str, NaverBlogClient, Crawler, FailureStore]:
    """블로그 대상의 클라이언트·크롤러를 구성한다."""
    try:
        blog_id = resolve_blog_id(target)
    except InvalidBlogReference as exc:
        raise click.BadParameter(str(exc), param_hint="TARGET") from exc

    logger.info("블로그 백업 시작: blog_id=%s out=%s", blog_id, out_dir)
    client = NaverBlogClient(blog_id, delay=delay, max_retries=max_retries)
    failures = FailureStore.load(out_dir)
    crawler = Crawler(client, out_dir, failures)
    title = f"[bold]네이버 블로그 백업[/bold] · blog_id=[cyan]{blog_id}[/cyan]"
    return title, client, crawler, failures


def _resolve_cli_cookie(cookie: str | None, cookie_file: Path | None) -> str | None:
    """카페 세션 쿠키를 정한다: --cookie(문자열) > --cookie-file > 저장된 쿠키.

    Raises:
        click.BadParameter: --cookie-file을 해석하지 못한 경우.
    """
    if cookie:
        return cookie
    if cookie_file is not None:
        try:
            resolved = parse_cookie_file(cookie_file)
        except InvalidCookieFile as exc:
            raise click.BadParameter(str(exc), param_hint="--cookie-file") from exc
        console.print(f"[dim]쿠키 파일에서 세션을 읽었습니다: {cookie_file}[/dim]")
        return resolved
    stored = load_cookie()
    if stored:
        console.print("[dim]저장된 쿠키를 사용합니다(GUI에서 저장한 세션).[/dim]")
    return stored


def _build_cafe(
    target: str,
    out_dir: Path,
    *,
    delay: float,
    max_retries: int,
    cookie: str | None,
    menu_id: int | None,
) -> tuple[str, NaverCafeClient, Crawler, FailureStore]:
    """카페 대상의 클라이언트·크롤러를 구성한다(카페 본문 파서 주입)."""
    try:
        ref = resolve_cafe_reference(target)
    except InvalidCafeReference as exc:
        raise click.BadParameter(str(exc), param_hint="TARGET") from exc

    logger.info("카페 백업 시작: ref=%s out=%s menu=%s", ref, out_dir, menu_id)
    if not cookie:
        console.print(
            "[yellow]쿠키 미지정[/yellow] — 공개 게시판만 받을 수 있습니다. 로그인/등급 제한 "
            "게시판은 --cookie(문자열)나 --cookie-file(파일)로 세션 쿠키를 주입하세요."
        )
    client = NaverCafeClient(
        ref, cookie=cookie, menu_id=menu_id, delay=delay, max_retries=max_retries
    )
    failures = FailureStore.load(out_dir)
    crawler = Crawler(client, out_dir, failures, parse_body=parse_cafe_body)
    label = ref.club_url or (str(ref.cafe_id) if ref.cafe_id is not None else "?")
    title = f"[bold]네이버 카페 백업[/bold] · [cyan]{label}[/cyan]"
    return title, client, crawler, failures


def _backup(
    client: PostSource,
    crawler: Crawler,
    out_dir: Path,
    failures: FailureStore,
    *,
    limit: int | None,
    since: date | None,
    until: date | None,
    retry_flag: bool | None,
    force: bool,
) -> None:
    """계획 수립 → 요약 → 진행 → 결과 출력까지의 공통 백업 흐름."""
    with client:  # type: ignore[attr-defined]
        try:
            with console.status("[bold]글 목록 수집 중…[/bold]"):
                plan = crawler.build_plan(since=since, until=until)
        except (BlogNotFound, CafeNotFound) as exc:
            # 형식은 맞지만 없는 블로그/카페다. 입력 오류로 깔끔히 안내한다.
            raise click.BadParameter(str(exc), param_hint="TARGET") from exc
        except LoginRequired as exc:
            raise click.ClickException(str(exc)) from exc

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

    summary = (
        f"전체 글 [bold]{plan.total + plan.skipped_anniversary}[/bold]건 중 "
        f"대상 [bold]{plan.total}[/bold]건"
    )
    # '그날의 추억' 자동 노출 글은 블로그에만 있다. 있을 때만 덧붙인다.
    if plan.skipped_anniversary:
        summary += f" · '그날의 추억' 제외 [yellow]{plan.skipped_anniversary}[/yellow]건"
    console.print(summary)
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
