"""GUI 상태 갱신(렌더 틱 코얼레싱) 동작 테스트.

Flet 런타임 없이 ``_set_status``/``_flush_status``의 계약만 검증한다: 백그라운드에서
들어온 상태 변경은 즉시 컨트롤에 쓰지 않고 예약만 하며(deferred), 렌더 틱이 호출하는
``_flush_status``에서 최신 값 하나만 반영(coalescing)된다.
"""

from __future__ import annotations

import threading
import time

import flet as ft
import pytest

import naver_post_crawler.gui as gui_mod
from naver_post_crawler.gui import CrawlerGUI, _first_picked_path


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


def test_set_status_now_applies_immediately_without_tick() -> None:
    gui = _bare_gui()

    gui._set_status_now("블로그 아이디 또는 URL을 입력하세요.", "red")

    # 렌더 틱을 거치지 않고 곧바로 컨트롤에 반영된다.
    assert gui.status.value == "블로그 아이디 또는 URL을 입력하세요."  # type: ignore[attr-defined]
    assert gui.status.color == "red"  # type: ignore[attr-defined]
    assert gui.status.updates == 1  # type: ignore[attr-defined]
    # 틱을 쓰지 않으므로 dirty도 세우지 않는다(백그라운드 틱 스레드를 깨우지 않음).
    assert not gui._status_dirty.is_set()


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


def test_ui_ticker_flushes_before_throttle_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # 단발 상태 변경은 0.2초 throttle을 기다리지 않고 즉시 반영돼야 한다(leading edge).
    # 실제 sleep 없이 flush와 sleep의 호출 순서만 결정적으로 검증한다.
    gui = _bare_gui()
    events: list[str] = []

    def fake_sleep(_seconds: float) -> None:
        events.append("sleep")
        gui._app_closing.set()  # 한 바퀴 돈 뒤 루프를 끝낸다

    monkeypatch.setattr(gui_mod.time, "sleep", fake_sleep)
    original_flush = gui._flush_status

    def tracking_flush() -> None:
        events.append("flush")
        original_flush()

    monkeypatch.setattr(gui, "_flush_status", tracking_flush)

    gui._set_status("즉시 반영", "red")
    gui._ui_ticker()

    # flush가 throttle sleep보다 먼저 일어나고, 값도 곧바로 반영된다.
    assert events[0] == "flush"
    assert "sleep" in events
    assert events.index("flush") < events.index("sleep")
    assert gui.status.value == "즉시 반영"  # type: ignore[attr-defined]


class _FakePicked:
    def __init__(self, path: str | None) -> None:
        self.path = path


def test_first_picked_path_from_list() -> None:
    assert _first_picked_path([_FakePicked("/x/cookies.txt")]) == "/x/cookies.txt"


def test_first_picked_path_from_files_event() -> None:
    class _Event:
        files = [_FakePicked("/y/cookies.json")]

    assert _first_picked_path(_Event()) == "/y/cookies.json"


def test_first_picked_path_none_and_empty() -> None:
    assert _first_picked_path(None) is None
    assert _first_picked_path([]) is None

    class _Empty:
        files: list[object] = []

    assert _first_picked_path(_Empty()) is None


def test_ui_ticker_drains_final_status_on_shutdown() -> None:
    gui = _bare_gui()
    gui._set_status("완료", "green")
    # 이미 종료 신호가 선 상태로 진입하면 루프 본문은 건너뛰고 마지막 drain flush만
    # 수행돼야 한다(완료/중단 문구 누락 방지). 동기 호출이라 타이밍 경합이 없다.
    gui._app_closing.set()

    gui._ui_ticker()

    assert gui.status.value == "완료"  # type: ignore[attr-defined]
    assert gui.status.color == "green"  # type: ignore[attr-defined]


# -- 앱 내 웹뷰 네이버 로그인 버튼 배선 ---------------------------------------------------
# 실제 ft.Page 없이 실제 컨트롤을 만들면 미부착 컨트롤의 ``.page`` 접근이 예외를
# 던지므로(RuntimeError), _build()가 건드리는 표면만 흉내 내는 대역 페이지를 쓴다.


class _FakeWindow:
    """``page.window`` 대역 — 폭/높이 등 속성 대입만 받는다."""


class _FakeBuildPage:
    """``ft.Page`` 대역 — ``_build()``가 건드리는 표면(속성 대입·``services``·``add``)만
    흉내 낸다. 진짜 페이지 연결이 없으면 실제 컨트롤의 ``.page`` 접근 자체가 예외를
    던지므로, ``_build()`` 끝의 ``_refresh_failures``/``_refresh_cookie_status``(파일·
    환경을 건드림)는 호출부(테스트)에서 개별적으로 no-op 처리한다.
    """

    def __init__(self) -> None:
        self.services: list[object] = []
        self.window = _FakeWindow()
        self.added: tuple[object, ...] = ()

    def add(self, *controls: object) -> None:
        self.added = controls


def _bare_gui_with_build() -> CrawlerGUI:
    """``_build()``까지 실제로 실행해 컨트롤 배선을 검증하되, 파일/환경을 건드리는
    새로고침 호출은 no-op으로 막은 인스턴스를 만든다."""
    gui = object.__new__(CrawlerGUI)
    gui.page = _FakeBuildPage()  # type: ignore[assignment]
    gui._refresh_failures = lambda: None  # type: ignore[method-assign]
    gui._refresh_cookie_status = lambda: None  # type: ignore[method-assign]
    gui._build()
    return gui


def _walk_controls(node: object, seen: set[int] | None = None):
    """``page.add``에 넘긴 컨트롤 트리를 재귀 순회해 모든 컨트롤을 낸다.

    버튼을 만들기만 하고 트리에 붙이지 않으면 화면에 안 보이므로, 실제 마운트
    여부를 확인하려면 존재·배선만이 아니라 트리 도달 가능성을 봐야 한다.
    """
    if seen is None:
        seen = set()
    if node is None or isinstance(node, str) or id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk_controls(item, seen)
        return
    yield node
    for attr in ("controls", "content", "title", "subtitle", "leading", "trailing", "actions"):
        yield from _walk_controls(getattr(node, attr, None), seen)


def test_advanced_options_has_naver_login_button_wired_to_handler() -> None:
    # covers: Test-7
    gui = _bare_gui_with_build()

    assert hasattr(gui, "_cookie_login")
    assert gui.cookie_login_btn.content == "네이버 로그인"  # type: ignore[attr-defined]
    assert gui.cookie_login_btn.on_click == gui._cookie_login  # type: ignore[attr-defined]
    # 버튼이 실제로 빌드된 컨트롤 트리(고급 옵션)에 마운트됐는지 — 존재·배선만으로는
    # "만들었지만 안 붙임"을 못 잡으므로 트리 도달 가능성을 단언한다.
    mounted = list(_walk_controls(gui.page.added))  # type: ignore[attr-defined]
    assert any(c is gui.cookie_login_btn for c in mounted)  # type: ignore[attr-defined]


class _FakeRunThreadPage:
    """``page.run_thread`` 대역 — 대상 콜러블을 기록만 하고 실행하지 않는다.

    실제로 실행해 버리면 오프스레드 디스패치인지(동기 호출이 아닌지) 구분할 수
    없으므로, 호출을 기록만 하는 것이 검증의 핵심이다.
    """

    def __init__(self) -> None:
        self.run_thread_calls: list[object] = []

    def run_thread(self, target: object, *args: object) -> None:
        self.run_thread_calls.append(target)


def _bare_gui_with_run_thread_page() -> tuple[CrawlerGUI, _FakeRunThreadPage]:
    gui = object.__new__(CrawlerGUI)
    fake_page = _FakeRunThreadPage()
    gui.page = fake_page  # type: ignore[assignment]
    return gui, fake_page


def test_cookie_login_dispatches_off_thread_without_synchronous_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: Test-8
    gui, fake_page = _bare_gui_with_run_thread_page()
    login_calls: list[object] = []
    save_calls: list[object] = []
    monkeypatch.setattr(gui_mod, "login_and_capture", lambda *a, **kw: login_calls.append(1))
    monkeypatch.setattr(gui_mod, "save_cookie", lambda *a, **kw: save_calls.append(a))

    gui._cookie_login(object())

    # 오프스레드로 _run_cookie_login 하나만 예약하고, UI 스레드에서 캡처/저장을
    # 동기 실행하지 않는다.
    assert fake_page.run_thread_calls == [gui._run_cookie_login]
    assert login_calls == []
    assert save_calls == []


def test_run_cookie_login_saves_and_reports_success_when_header_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: Test-8
    gui, _ = _bare_gui_with_run_thread_page()
    status_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        gui, "_set_cookie_status", lambda msg, color: status_calls.append((msg, color))
    )
    save_calls: list[str] = []
    monkeypatch.setattr(gui_mod, "login_and_capture", lambda *a, **kw: "NID_AUT=a; NID_SES=b")
    monkeypatch.setattr(gui_mod, "save_cookie", lambda cookie, *a, **kw: save_calls.append(cookie))

    gui._run_cookie_login()

    assert save_calls == ["NID_AUT=a; NID_SES=b"]
    assert status_calls, "성공 상태 갱신이 있어야 한다"
    assert status_calls[-1][1] == ft.Colors.GREEN


def test_run_cookie_login_skips_save_and_reports_failure_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: Test-8
    gui, _ = _bare_gui_with_run_thread_page()
    status_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        gui, "_set_cookie_status", lambda msg, color: status_calls.append((msg, color))
    )
    save_calls: list[str] = []
    monkeypatch.setattr(gui_mod, "login_and_capture", lambda *a, **kw: None)
    monkeypatch.setattr(gui_mod, "save_cookie", lambda cookie, *a, **kw: save_calls.append(cookie))

    gui._run_cookie_login()

    # None이면 저장하지 않아 기존 쿠키를 보존한다.
    assert save_calls == []
    assert status_calls, "실패/취소 상태 갱신이 있어야 한다"
    assert status_calls[-1][1] == ft.Colors.RED


def test_cookie_login_ignores_reentrant_click_while_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: Test-9 (R3 반영 — 진행 중 재클릭이 로그인 창을 또 띄우지 않게 재진입 가드)
    gui, fake_page = _bare_gui_with_run_thread_page()
    monkeypatch.setattr(gui_mod, "login_and_capture", lambda *a, **kw: None)
    monkeypatch.setattr(gui_mod, "save_cookie", lambda *a, **kw: None)

    gui._cookie_login(object())
    # 첫 클릭이 아직 진행 중(fake run_thread가 _run_cookie_login을 실행하지 않아 플래그
    # 미해제)일 때의 재클릭은 무시돼 두 번째 디스패치가 없어야 한다.
    gui._cookie_login(object())

    assert fake_page.run_thread_calls == [gui._run_cookie_login]
