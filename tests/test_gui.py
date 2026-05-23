"""GUI 상태 갱신(렌더 틱 코얼레싱) 동작 테스트.

Flet 런타임 없이 ``_set_status``/``_flush_status``의 계약만 검증한다: 백그라운드에서
들어온 상태 변경은 즉시 컨트롤에 쓰지 않고 예약만 하며(deferred), 렌더 틱이 호출하는
``_flush_status``에서 최신 값 하나만 반영(coalescing)된다.
"""

from __future__ import annotations

import threading
import time

import pytest

import naver_blog_crawler.gui as gui_mod
from naver_blog_crawler.gui import CrawlerGUI


class _FakeText:
    """``ft.Text`` 대역 — value/color/page와 update 호출 횟수만 흉내 낸다."""

    def __init__(self) -> None:
        self.value: str | None = None
        self.color: str | None = None
        self.page: object | None = object()  # None이면 flush가 건너뛰는 가드를 탄다
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


def _bare_gui() -> CrawlerGUI:
    """``__init__``(``_build``·렌더 스레드)을 거치지 않고 상태 필드만 갖춘 인스턴스."""
    gui = object.__new__(CrawlerGUI)
    gui._status_lock = threading.Lock()
    gui._status_dirty = threading.Event()
    gui._app_closing = threading.Event()
    gui._status_msg = "대기 중"
    gui._status_color = None
    gui.status = _FakeText()  # type: ignore[assignment]
    return gui


def test_set_status_defers_until_flush() -> None:
    gui = _bare_gui()

    gui._set_status("수집 중… 3개", "red")

    # 예약만 하고 컨트롤에는 아직 쓰지 않는다.
    assert gui.status.value is None  # type: ignore[attr-defined]
    assert gui.status.updates == 0  # type: ignore[attr-defined]
    assert gui._status_dirty.is_set()

    gui._flush_status()

    assert gui.status.value == "수집 중… 3개"  # type: ignore[attr-defined]
    assert gui.status.color == "red"  # type: ignore[attr-defined]
    assert gui.status.updates == 1  # type: ignore[attr-defined]


def test_flush_coalesces_to_latest_value() -> None:
    gui = _bare_gui()

    for count in range(1, 101):
        gui._set_status(f"수집 중… {count}개")

    # 100번의 변경이 한 번의 flush로 합쳐져 최신 값만 반영된다.
    gui._flush_status()

    assert gui.status.value == "수집 중… 100개"  # type: ignore[attr-defined]
    assert gui.status.updates == 1  # type: ignore[attr-defined]


def test_flush_skips_when_control_not_on_page() -> None:
    gui = _bare_gui()
    gui.status.page = None  # type: ignore[attr-defined]

    gui._set_status("완료", "green")
    gui._flush_status()

    # 페이지에 붙지 않은 컨트롤은 건드리지 않는다(가드).
    assert gui.status.value is None  # type: ignore[attr-defined]
    assert gui.status.updates == 0  # type: ignore[attr-defined]


def test_ui_ticker_applies_latest_and_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    # 렌더 틱을 0초로 줄여 루프가 즉시 한 바퀴 돌게 한다(테스트 자체의 sleep은 보존).
    monkeypatch.setattr(gui_mod, "_UI_TICK_SECONDS", 0.0)
    gui = _bare_gui()

    thread = threading.Thread(target=gui._ui_ticker, daemon=True)
    thread.start()
    try:
        gui._set_status("수집 중… 42개")
        deadline = time.monotonic() + 2.0
        while gui.status.value != "수집 중… 42개" and time.monotonic() < deadline:  # type: ignore[attr-defined]
            time.sleep(0.01)
        assert gui.status.value == "수집 중… 42개"  # type: ignore[attr-defined]
    finally:
        # 종료 신호 + dirty로 wait()를 깨워 루프를 끝낸다.
        gui._app_closing.set()
        gui._status_dirty.set()
        thread.join(timeout=2.0)

    assert not thread.is_alive()


def test_ui_ticker_drains_final_status_on_shutdown() -> None:
    gui = _bare_gui()
    gui._set_status("완료", "green")
    # 이미 종료 신호가 선 상태로 진입하면 루프 본문은 건너뛰고 마지막 drain flush만
    # 수행돼야 한다(완료/중단 문구 누락 방지). 동기 호출이라 타이밍 경합이 없다.
    gui._app_closing.set()

    gui._ui_ticker()

    assert gui.status.value == "완료"  # type: ignore[attr-defined]
    assert gui.status.color == "green"  # type: ignore[attr-defined]
