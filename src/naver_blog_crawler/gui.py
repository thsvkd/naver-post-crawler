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
from collections import Counter
from pathlib import Path

import flet as ft

from .blog_id import resolve_blog_id
from .client import NaverBlogClient
from .crawler import Crawler, Outcome
from .errors import CrawlerError, InvalidBlogReference
from .failures import FailureStore
from .log import setup_logging

logger = logging.getLogger(__name__)

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


class CrawlerGUI:
    """GUI 상태와 이벤트 처리를 담는 컨트롤러."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._stop = threading.Event()
        self._build()

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

        self.blog_field = ft.TextField(
            label="블로그 아이디 또는 URL",
            hint_text="winter9377  또는  https://m.blog.naver.com/winter9377",
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
        browse_btn = ft.Button("찾아보기", icon=ft.Icons.FOLDER_OPEN, on_click=self._pick_folder)

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
                    padding=ft.Padding(left=16, top=4, right=16, bottom=12),
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
                    ft.Text("네이버 블로그 전체 글 백업", size=22, weight=ft.FontWeight.BOLD),
                    self.blog_field,
                    ft.Row(
                        [self.out_field, browse_btn],
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    self.retry_cb,
                    advanced,
                    ft.Row([self.start_btn, self.stop_btn, self.open_btn]),
                    self.progress,
                    self.status,
                    self.counts_text,
                    ft.Container(
                        content=self.log_view,
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
        self._set_running(True)
        self.log_view.controls.clear()
        self.progress.value = 0
        self.counts_text.value = ""
        self.page.update()
        try:
            options = self._read_options()
        except ValueError as exc:
            self._set_status(str(exc), ft.Colors.RED)
            self._set_running(False)
            return

        setup_logging(options["log_dir"], level=options["log_level"])
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
                self._set_status("글 목록 수집 중…")
                plan = crawler.build_plan()
                total = plan.total
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
        self.page.update()

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
        for control in (self.blog_field, self.out_field, self.force_cb):
            control.disabled = running
        self.page.update()

    def _set_status(self, message: str, color: str | None = None) -> None:
        self.status.value = message
        self.status.color = color
        self.page.update()


def _view(page: ft.Page) -> None:
    CrawlerGUI(page)


def main() -> None:
    """GUI 실행 진입점(``naver-blog-crawler-gui``)."""
    ft.run(_view)


if __name__ == "__main__":
    main()
