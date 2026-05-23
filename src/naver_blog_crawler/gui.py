"""Flet 기반 데스크톱 GUI.

CLI와 동일한 코어(:class:`Crawler` 제너레이터, :class:`FailureStore`,
:func:`resolve_blog_id`)를 재사용하는 얇은 표현 계층이다. 크롤링은 백그라운드
스레드에서 돌리고, 진행바·집계·최근 결과 로그를 실시간으로 갱신한다.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import flet as ft
from rich.console import Console

from .blog_id import resolve_blog_id
from .client import NaverBlogClient
from .crawler import Crawler, Outcome
from .errors import CrawlerError, InvalidBlogReference
from .failures import FailureStore
from .log import setup_logging

logger = logging.getLogger(__name__)

# GUI는 자체 결과 로그를 보여주지만, 터미널에서 실행한 경우 자세한 로그를 함께
# 보고 싶을 수 있으므로 stderr 콘솔에도 출력한다. 패키지된 창 모드 앱은 stderr가
# 없을 수 있으므로(None) 그때는 콘솔 출력을 끄고 파일에만 기록한다.
_console = Console(stderr=True) if sys.stderr is not None else None

# 결과 종류별 (라벨, 색).
_OUTCOME_STYLE: dict[Outcome, tuple[str, str]] = {
    Outcome.WRITTEN: ("저장", ft.Colors.GREEN),
    Outcome.SKIPPED_EXISTING: ("기존", ft.Colors.CYAN),
    Outcome.SKIPPED_EMPTY: ("빈 글", ft.Colors.AMBER),
    Outcome.SKIPPED_FAILED: ("이전 실패", ft.Colors.PURPLE),
    Outcome.FAILED: ("실패", ft.Colors.RED),
}
# 로그 ListView에 유지할 최대 줄 수(메모리 보호).
_MAX_LOG_ROWS = 200
# 상태 텍스트 렌더 틱 주기(초). 수집 카운트처럼 빠르게 바뀌는 값의 변경을 이 창
# 안에서 한 번으로 합쳐 화면이 밀리지 않게 한다.
_UI_TICK_SECONDS = 0.2


class CrawlerGUI:
    """GUI 상태와 이벤트 처리를 담는 컨트롤러."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._stop = threading.Event()
        # 상태 텍스트는 백그라운드 스레드가 값만 기록(이벤트)하고, 렌더 틱이
        # 0.2초마다 일괄 반영한다. _status_lock으로 메시지·색을 한 쌍으로 보호하고,
        # _status_dirty로 변경을 알린다.
        self._status_lock = threading.Lock()
        self._status_dirty = threading.Event()
        self._app_closing = threading.Event()
        self._status_msg = "대기 중"
        self._status_color: str | None = None
        self._build()
        # 데몬 스레드라 창을 닫으면 함께 종료된다.
        self._render_thread = threading.Thread(
            target=self._ui_ticker, name="gui-render-tick", daemon=True
        )
        self._render_thread.start()

    # -- UI 구성 ---------------------------------------------------------
    def _build(self) -> None:
        page = self.page
        page.title = "네이버 블로그 백업"
        page.theme_mode = ft.ThemeMode.SYSTEM
        page.padding = 20
        page.window.width = 760
        page.window.height = 720
        page.window.min_width = 560
        page.window.min_height = 520
        page.on_close = self._on_close

        # 포커스 전에는 label("블로그 아이디 또는 URL")이, 포커스하면 hint(예시 형식)가
        # 보인다. 둘 다 입력값과 구분되도록 연하게 표시하고, hint는 특정 블로그가 아닌
        # 일반적인 형식 예시로 둔다.
        _muted = ft.TextStyle(color=ft.Colors.with_opacity(0.6, ft.Colors.ON_SURFACE))
        self.blog_field = ft.TextField(
            label="블로그 아이디 또는 URL",
            label_style=_muted,
            hint_text="예: myblog  또는  https://m.blog.naver.com/myblog",
            hint_style=_muted,
            expand=True,
            on_submit=lambda _e: self._start(),
        )
        self.out_field = ft.TextField(
            label="출력 폴더",
            value="output",
            expand=True,
            on_change=lambda _e: self._refresh_failures(),
        )
        self.file_picker = ft.FilePicker()
        page.services.append(self.file_picker)
        self.browse_btn = ft.Button(
            "찾아보기", icon=ft.Icons.FOLDER_OPEN, on_click=self._pick_folder
        )

        self.retry_cb = ft.Checkbox(label="이전 실패 글 다시 시도", value=True, visible=False)

        self.start_btn = ft.Button(
            "시작", icon=ft.Icons.PLAY_ARROW, on_click=lambda _e: self._start()
        )
        self.stop_btn = ft.Button(
            "중단", icon=ft.Icons.STOP, on_click=lambda _e: self._stop.set(), disabled=True
        )
        self.open_btn = ft.Button(
            "폴더 열기", icon=ft.Icons.FOLDER, on_click=self._open_folder, disabled=True
        )

        # 고급 옵션(접이식).
        self.delay_field = ft.TextField(label="딜레이(초)", value="0.5", width=130)
        self.retries_field = ft.TextField(label="최대 재시도", value="3", width=130)
        self.force_cb = ft.Checkbox(label="이미 저장된 글도 다시 받기", value=False)
        self.loglevel_dd = ft.Dropdown(
            label="로그 레벨",
            value="INFO",
            width=160,
            options=[ft.dropdown.Option(x) for x in ("DEBUG", "INFO", "WARNING", "ERROR")],
        )
        advanced = ft.ExpansionTile(
            title=ft.Text("고급 옵션"),
            controls=[
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Row([self.delay_field, self.retries_field, self.loglevel_dd]),
                            self.force_cb,
                        ],
                        spacing=10,
                    ),
                    # 상단 여백을 넉넉히 둬 첫 입력칸의 떠오른 label("딜레이(초)")이
                    # ExpansionTile 타이틀 행에 가려지지 않게 한다(controls_padding 기본 0).
                    padding=ft.Padding(left=16, top=20, right=16, bottom=12),
                )
            ],
        )

        self.progress = ft.ProgressBar(value=0)
        self.status = ft.Text("대기 중", size=13)
        self.counts_text = ft.Text("", size=13, color=ft.Colors.GREY)
        self.log_view = ft.ListView(expand=True, spacing=2, auto_scroll=True)

        page.add(
            ft.Column(
                [
                    ft.Text("네이버 블로그 크롤러", size=22, weight=ft.FontWeight.BOLD),
                    self.blog_field,
                    ft.Row(
                        [self.out_field, self.browse_btn],
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    self.retry_cb,
                    advanced,
                    ft.Row([self.start_btn, self.stop_btn, self.open_btn]),
                    self.progress,
                    self.status,
                    self.counts_text,
                    ft.Container(
                        # SelectionArea로 감싸 로그 내용을 드래그로 선택·복사할 수 있게 한다.
                        content=ft.SelectionArea(content=self.log_view),
                        expand=True,
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        border_radius=8,
                        padding=8,
                    ),
                ],
                spacing=12,
                expand=True,
            )
        )
        self._refresh_failures()

    # -- 이벤트 ----------------------------------------------------------
    async def _pick_folder(self, _e: ft.ControlEvent) -> None:
        path = await self.file_picker.get_directory_path(dialog_title="출력 폴더 선택")
        if path:
            self.out_field.value = path
            self.out_field.update()
            self._refresh_failures()

    def _open_folder(self, _e: ft.ControlEvent) -> None:
        path = Path(self.out_field.value.strip() or "output").resolve()
        if not path.exists():
            return
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    def _on_close(self, _e: ft.ControlEvent) -> None:
        """창이 닫히면 렌더 틱 스레드를 깨워 깔끔히 종료시킨다."""
        self._app_closing.set()
        self._status_dirty.set()

    def _refresh_failures(self) -> None:
        """현재 출력 폴더의 이전 실패 건수에 따라 재시도 체크박스를 보인다."""
        out_dir = Path(self.out_field.value.strip() or "output")
        try:
            count = len(FailureStore.load(out_dir))
        except CrawlerError:
            count = 0
        self.retry_cb.visible = count > 0
        self.retry_cb.label = f"이전 실패 {count}건 다시 시도"
        if self.retry_cb.page is not None:
            self.retry_cb.update()

    def _start(self) -> None:
        if not self.blog_field.value.strip():
            self._set_status("블로그 아이디 또는 URL을 입력하세요.", ft.Colors.RED)
            return
        self._stop.clear()
        self.page.run_thread(self._crawl)

    # -- 크롤링(백그라운드 스레드) ---------------------------------------
    def _crawl(self) -> None:
        # 시작과 동시에 이전 실행의 결과·에러 표시를 모두 비운다. status도 함께
        # 초기화해야 직전 에러 문구(예: "블로그 아이디를 입력하세요")가 수집이
        # 시작될 때까지 빨갛게 남아 보이지 않는다.
        self.log_view.controls.clear()
        self.progress.value = 0
        self.counts_text.value = ""
        self.status.value = "시작 준비 중…"
        self.status.color = None
        # 입력 비활성화와 위 초기화를 page.update로 한 번에 반영한다.
        self._set_running(True)
        try:
            options = self._read_options()
        except ValueError as exc:
            self._set_status(str(exc), ft.Colors.RED)
            self._set_running(False)
            return

        setup_logging(options["log_dir"], level=options["log_level"], console=_console)
        try:
            blog_id = resolve_blog_id(self.blog_field.value)
        except InvalidBlogReference as exc:
            self._set_status(str(exc), ft.Colors.RED)
            self._set_running(False)
            return

        out_dir: Path = options["out_dir"]
        counts: Counter[Outcome] = Counter()
        interrupted = False
        try:
            with NaverBlogClient(
                blog_id, delay=options["delay"], max_retries=options["max_retries"]
            ) as client:
                failures = FailureStore.load(out_dir)
                crawler = Crawler(
                    client,
                    out_dir,
                    failures,
                    force=options["force"],
                    retry_failed=self.retry_cb.value,
                )
                # 수집은 전체 건수를 미리 알 수 없으므로 진행바를 indeterminate로 두고
                # 모은 글 수를 실시간으로 보여준다.
                self.progress.value = None
                self.progress.update()
                self._set_status("글 목록 수집 중… 0개")
                plan = crawler.build_plan(on_collect=self._on_collect)
                total = plan.total
                self.progress.value = 0
                self.progress.update()
                self._set_status(f"대상 {total}건 백업 중…")
                for done, result in enumerate(crawler.run(plan), start=1):
                    if self._stop.is_set():
                        interrupted = True
                        break
                    counts[result.outcome] += 1
                    self._on_result(done, total, result, counts)
                failures.save()
        except CrawlerError as exc:
            self._set_status(f"오류: {exc}", ft.Colors.RED)
            self._set_running(False)
            return

        self._finish(counts, interrupted)

    def _read_options(self) -> dict[str, object]:
        try:
            delay = float(self.delay_field.value)
            max_retries = int(self.retries_field.value)
        except (TypeError, ValueError) as exc:
            raise ValueError("딜레이·최대 재시도 값이 올바르지 않습니다.") from exc
        return {
            "out_dir": Path(self.out_field.value.strip() or "output"),
            "delay": delay,
            "max_retries": max_retries,
            "force": bool(self.force_cb.value),
            "log_dir": Path("logs"),
            "log_level": logging.getLevelNamesMapping()[self.loglevel_dd.value],
        }

    # -- UI 갱신 헬퍼(스레드에서 호출) -----------------------------------
    def _on_collect(self, count: int) -> None:
        """글 목록 수집 진행 콜백 — 모은 건수를 실시간으로 보여준다."""
        self._set_status(f"글 목록 수집 중… {count}개")

    def _on_result(self, done: int, total: int, result: object, counts: Counter[Outcome]) -> None:
        outcome = result.outcome  # type: ignore[attr-defined]
        label, color = _OUTCOME_STYLE[outcome]
        seq = result.seq  # type: ignore[attr-defined]
        title = result.meta.title[:48]  # type: ignore[attr-defined]
        row = ft.Row(
            [
                ft.Text(f"{seq:04d}", size=12, color=ft.Colors.GREY, width=46),
                ft.Text(label, size=12, color=color, width=72),
                ft.Text(title, size=12, expand=True, no_wrap=True),
            ],
            spacing=8,
        )
        self.log_view.controls.append(row)
        if len(self.log_view.controls) > _MAX_LOG_ROWS:
            del self.log_view.controls[0]
        self.progress.value = done / total if total else None
        self.counts_text.value = self._counts_line(counts)
        # 페이지 전체가 아니라 바뀐 컨트롤만 갱신해 한 건씩 즉시(실시간) 반영한다.
        # (page.update()는 전체를 스캔/전송해 무겁고, 빠른 연속 호출 시 뭉쳐 보인다.)
        self.log_view.update()
        self.progress.update()
        self.counts_text.update()

    def _counts_line(self, counts: Counter[Outcome]) -> str:
        parts = [
            f"{label} {counts.get(outcome, 0)}"
            for outcome, (label, _color) in _OUTCOME_STYLE.items()
        ]
        return "  ·  ".join(parts)

    def _finish(self, counts: Counter[Outcome], interrupted: bool) -> None:
        if interrupted:
            self._set_status(
                "중단됨 — 받은 글은 저장되었습니다. 다시 시작하면 이어서 진행합니다.",
                ft.Colors.AMBER,
            )
        else:
            self._set_status("완료", ft.Colors.GREEN)
        self.counts_text.value = self._counts_line(counts)
        self.open_btn.disabled = False
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.start_btn.disabled = running
        self.stop_btn.disabled = not running
        # 실행 중에는 입력·출력 폴더 선택을 잠가 진행 중인 작업과 어긋나지 않게 한다.
        for control in (self.blog_field, self.out_field, self.browse_btn, self.force_cb):
            control.disabled = running
        self.page.update()

    def _set_status(self, message: str, color: str | None = None) -> None:
        """상태 텍스트 갱신을 예약한다.

        실제 반영은 렌더 틱(:meth:`_ui_ticker`, 0.2초)에서 일괄 처리한다. 수집
        루프처럼 초당 수십~수백 번 호출돼도 화면 갱신은 0.2초마다 한 번으로
        합쳐져(최신 값만 반영) 화면이 밀리지 않는다.
        """
        with self._status_lock:
            self._status_msg = message
            self._status_color = color
        self._status_dirty.set()

    def _ui_ticker(self) -> None:
        """0.2초마다 누적된 상태 변경을 한 번에 UI에 반영하는 렌더 루프.

        변경이 없으면 :attr:`_status_dirty`에서 블록해 유휴 시 깨어나지 않는다.
        변경이 들어오면 dirty를 먼저 내린 뒤 0.2초를 기다려 그 사이의 연속 변경을
        모아(코얼레싱) 최신 값만 반영한다. dirty를 sleep 전에 내려야, 대기 중 들어온
        변경이 dirty를 다시 세워 다음 루프에서 누락 없이 반영된다. 데몬 스레드이므로
        창이 닫히면 함께 종료되며, 종료 직전 마지막 상태를 한 번 더 반영(drain)해
        완료·중단 문구가 누락되지 않게 한다.
        """
        while not self._app_closing.is_set():
            self._status_dirty.wait()
            if self._app_closing.is_set():
                break
            self._status_dirty.clear()
            time.sleep(_UI_TICK_SECONDS)
            self._flush_status()
        self._flush_status()

    def _flush_status(self) -> None:
        """예약된 최신 상태 텍스트를 실제 컨트롤에 반영한다(렌더 틱 전용)."""
        with self._status_lock:
            message, color = self._status_msg, self._status_color
        if self.status.page is None:
            return
        self.status.value = message
        self.status.color = color
        try:
            self.status.update()
        except Exception:
            # 창 종료 중 컨트롤/연결이 해제되면 update가 실패할 수 있다. 데몬
            # 스레드가 조용히 죽어 이후 상태 갱신이 멈추는 것을 막고자 흡수하되,
            # 원인 추적이 가능하도록 디버그 로그로 남긴다(조용한 무시 아님).
            logger.debug("상태 텍스트 갱신 실패(창 종료 중일 수 있음)", exc_info=True)


def _view(page: ft.Page) -> None:
    CrawlerGUI(page)


def main() -> None:
    """GUI 실행 진입점(``naver-blog-crawler-gui``)."""
    ft.run(_view)


if __name__ == "__main__":
    main()
