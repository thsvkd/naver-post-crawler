"""GUI 상태 갱신(렌더 틱 코얼레싱) 동작 테스트.

Flet 런타임 없이 ``_set_status``/``_flush_status``의 계약만 검증한다: 백그라운드에서
들어온 상태 변경은 즉시 컨트롤에 쓰지 않고 예약만 하며(deferred), 렌더 틱이 호출하는
``_flush_status``에서 최신 값 하나만 반영(coalescing)된다.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

import naver_post_crawler.cookie_login as cookie_login_mod
import naver_post_crawler.gui as gui_mod
from naver_post_crawler.cookie import CookieMigration, MigrationResult, app_data_dir
from naver_post_crawler.errors import CredentialStoreError
from naver_post_crawler.gui import CrawlerGUI, _first_picked_path


def _helper_const(name: str) -> str:
    """W-4가 ``cookie_login``에 신설할 상수를 읽는다(수집 오류 대신 단언 실패로 드러나게)."""
    assert hasattr(cookie_login_mod, name), (
        f"cookie_login에 {name} 상수가 있어야 한다 — 헬퍼 모드는 환경변수로 전달한다(W-4)."
    )
    return getattr(cookie_login_mod, name)


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
    새로고침 호출은 no-op으로 막은 인스턴스를 만든다.

    ``__init__``을 건너뛰므로 ``_build()``는 영속 설정을 스스로 읽어야 한다
    (:func:`gui_mod._load_gui_settings`). 이 호출자는 앱을 새로 여는 것과 같다.
    """
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
    # 저장 성공 경로가 _refresh_cookie_status를 지나므로 이관 결과가 필요하다.
    gui._migration = MigrationResult(
        CookieMigration.NOTHING, exposed=False, path=Path("/nonexistent/cafe.txt")
    )
    gui._muted_color = None
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


# -- 헬퍼 진입 판정(W-4) ----------------------------------------------------------------
# 주의: 여기부터의 covers 태그는 docs/handoff-velopack-migration.md §5 번호다. 위쪽
# 테스트들의 Test-7~9는 앱 내 웹뷰 로그인 기능의 이전 핸드오프 번호이며 별개 체계다.
#
# main()은 GUI와 로그인 웹뷰 헬퍼가 갈라지는 유일한 지점이다. 배포본에서는 argv를 쓸 수
# 없으므로(flet 러너의 '인자 있으면 개발자 모드' 분기) 환경변수만 보고 갈라져야 한다.


def test_main_enters_helper_when_env_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-29
    monkeypatch.setenv(_helper_const("HELPER_ENV"), _helper_const("HELPER_MODE"))
    monkeypatch.setattr(gui_mod, "run_helper", lambda *_a, **_kw: 7)
    monkeypatch.setattr(
        gui_mod.ft, "run", lambda *_a, **_kw: pytest.fail("헬퍼 모드에서 GUI를 띄우면 안 된다")
    )

    with pytest.raises(SystemExit) as excinfo:
        gui_mod.main()

    assert excinfo.value.code == 7


def test_main_ignores_legacy_flag_in_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-29 (argv는 더 이상 판정 근거가 아니다)
    monkeypatch.delenv(_helper_const("HELPER_ENV"), raising=False)
    monkeypatch.setattr(gui_mod.sys, "argv", ["app", "--__cookie-login", "/tmp/cookies.json"])
    monkeypatch.setattr(
        gui_mod, "run_helper", lambda *_a, **_kw: pytest.fail("argv로 헬퍼에 진입하면 안 된다")
    )
    run_calls: list[object] = []
    monkeypatch.setattr(gui_mod.ft, "run", lambda view, *_a, **_kw: run_calls.append(view))

    gui_mod.main()

    assert run_calls == [gui_mod._view]


# -- 경로 정책(W-5): 로그·출력 폴더 -----------------------------------------------------
# Velopack 설치본은 바로가기로 실행돼 cwd가 보장되지 않고, 업데이트가 설치 폴더를 통째로
# 교체한다. 로그는 앱 데이터 아래 절대 경로로, 출력 폴더 기본값은 사용자 폴더로 간다(D-12).


def _options_gui(out_value: str) -> CrawlerGUI:
    """``_read_options()``가 읽는 입력 컨트롤만 대역으로 갖춘 인스턴스."""
    gui = object.__new__(CrawlerGUI)
    gui.delay_field = SimpleNamespace(value="1.0")  # type: ignore[assignment]
    gui.retries_field = SimpleNamespace(value="3")  # type: ignore[assignment]
    gui.menu_field = SimpleNamespace(value="")  # type: ignore[assignment]
    gui.out_field = SimpleNamespace(value=out_value)  # type: ignore[assignment]
    gui.force_cb = SimpleNamespace(value=False)  # type: ignore[assignment]
    gui.cookie_field = SimpleNamespace(value="")  # type: ignore[assignment]
    gui.loglevel_dd = SimpleNamespace(value="INFO")  # type: ignore[assignment]
    return gui


def test_log_dir_is_absolute_under_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-26
    storage = tmp_path / "storage"
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(storage))

    log_dir = _options_gui(str(tmp_path / "out"))._read_options()["log_dir"]

    assert isinstance(log_dir, Path)
    assert log_dir != Path("logs"), "cwd 상대 'logs' 하드코딩은 설치본에서 업데이트마다 소실된다"
    assert log_dir.is_absolute()
    assert log_dir == app_data_dir() / "logs"


def test_log_dir_does_not_follow_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-26 (cwd를 옮겨도 같은 절대 경로여야 한다)
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path / "storage"))
    first = _options_gui(str(tmp_path / "out"))._read_options()["log_dir"]
    assert isinstance(first, Path)
    # 상대 경로면 이 시점(현재 cwd)의 해석과 chdir 이후의 해석이 달라진다.
    first_resolved = first.resolve()

    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    second = _options_gui(str(tmp_path / "out"))._read_options()["log_dir"]
    assert isinstance(second, Path)

    assert first == second
    assert first_resolved == second.resolve(), (
        "cwd가 바뀌면 가리키는 곳이 달라지는 상대 경로다 — 설치본에서 로그가 흩어진다"
    )


def test_default_output_dir_is_absolute_user_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-27
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path / "storage"))

    default = gui_mod._default_output_dir()

    assert default != "output", "cwd 상대 기본값은 설치본에서 current\\output에 쌓인다"
    assert Path(default).is_absolute()
    # 사용자 폴더 기반이어야 한다(앱 데이터/설치 폴더 안이 아님).
    assert Path(default).is_relative_to(Path.home())


def test_gui_settings_live_under_app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-27 (영속 위치가 앱 데이터 아래여야 업데이트로 교체돼도 살아남는다)
    storage = tmp_path / "storage"
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(storage))

    assert gui_mod._gui_settings_path().parent == app_data_dir()

    gui_mod._save_gui_settings({"output_dir": str(tmp_path / "backup")})

    assert gui_mod._gui_settings_path().is_file()
    assert gui_mod._load_gui_settings()["output_dir"] == str(tmp_path / "backup")


class _FakeDirectoryPicker:
    """``ft.FilePicker`` 대역 — 폴더 선택 대화상자가 고른 경로를 그대로 돌려준다."""

    def __init__(self, path: str | None) -> None:
        self._path = path

    async def get_directory_path(self, **_kwargs: object) -> str | None:
        return self._path


def test_output_dir_defaults_then_persists_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-27
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path / "storage"))

    # 한 번도 고른 적이 없으면 사용자 폴더 기반 절대 경로가 기본값이다.
    first = _bare_gui_with_build()
    assert first.out_field.value == gui_mod._default_output_dir()

    # 사용자가 폴더를 고른다. out_field는 실제 페이지에 붙지 않은 컨트롤이라
    # update() 호출이 예외를 던지므로 대역으로 바꾼다.
    picked = str(tmp_path / "내 백업")
    first.out_field = SimpleNamespace(value="", update=lambda: None)  # type: ignore[assignment]
    first.file_picker = _FakeDirectoryPicker(picked)  # type: ignore[assignment]
    asyncio.run(first._pick_folder(None))  # type: ignore[arg-type]

    assert first.out_field.value == picked

    # 앱을 다시 연다 — 직전 선택이 복원돼야 한다.
    second = _bare_gui_with_build()
    assert second.out_field.value == picked


# -- 자격증명 보관소 실패와 이관 결과 안내 -----------------------------------
# 주의: 여기부터의 covers 태그는 ``cred/`` 접두사를 붙여 docs/handoff-credential-storage.md
# 의 인수 기준 번호임을 밝힌다. 이 파일 위쪽의 Test-3~27은 별개 핸드오프의 번호 체계라,
# 접두사 없이 쓰면 같은 번호가 서로 다른 기준을 가리켜 추적이 불가능해진다.
#
# 저장 실패를 성공으로 표시하면 사용자는 백업이 왜 안 되는지 알 수 없고, 이관 실패를
# 알리지 않으면 업데이트 후 갑자기 로그아웃된 이유를 알 방법이 없다. 두 분기 모두
# 화면 문구가 유일한 전달 수단이므로 여기서 고정한다.


def _raise_store_error(*_args: object, **_kwargs: object) -> None:
    raise CredentialStoreError("보관소 접근 거부")


def test_update_cookie_reports_store_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-5
    gui = _bare_gui()
    status_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        gui, "_set_cookie_status", lambda msg, color: status_calls.append((msg, color))
    )
    monkeypatch.setattr(gui_mod, "parse_cookie_file", lambda _p: "NID_AUT=a")
    monkeypatch.setattr(gui_mod, "save_cookie", _raise_store_error)

    gui._update_cookie(tmp_path / "cookies.txt")

    assert status_calls, "저장 실패를 알려야 한다"
    assert status_calls[-1][1] == ft.Colors.RED
    assert "저장" in status_calls[-1][0]


def test_run_cookie_login_reports_store_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: cred/Test-5 (로그인은 됐는데 저장이 실패한 경우 — 성공으로 표시하면 안 된다)
    gui, _ = _bare_gui_with_run_thread_page()
    status_calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        gui, "_set_cookie_status", lambda msg, color: status_calls.append((msg, color))
    )
    monkeypatch.setattr(gui_mod, "login_and_capture", lambda *a, **kw: "NID_AUT=a; NID_SES=b")
    monkeypatch.setattr(gui_mod, "save_cookie", _raise_store_error)

    gui._run_cookie_login()

    assert status_calls[-1][1] == ft.Colors.RED
    assert not gui._cookie_login_busy, "실패해도 재진입 가드는 풀려야 한다"


def _result(
    outcome: CookieMigration = CookieMigration.NOTHING,
    *,
    exposed: bool = False,
    path: Path | None = None,
) -> MigrationResult:
    return MigrationResult(outcome, exposed=exposed, path=path or Path("/nonexistent/cafe.txt"))


def _cookie_status_gui(monkeypatch: pytest.MonkeyPatch, migration: MigrationResult):
    gui = _bare_gui()
    gui._migration = migration
    gui._muted_color = None
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(gui, "_set_cookie_status", lambda msg, color: calls.append((msg, color)))
    return gui, calls


def test_cookie_status_warns_after_a_lost_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: cred/Test-12b
    gui, calls = _cookie_status_gui(monkeypatch, _result(CookieMigration.LOST))
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: None)

    gui._refresh_cookie_status()

    assert calls[-1][1] == ft.Colors.RED
    assert "네이버 로그인" in calls[-1][0], "무엇을 해야 하는지 알려야 한다"


def test_cookie_status_stays_neutral_when_nothing_was_migrated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: cred/Test-12b (평문 파일이 없던 대부분의 실행에서 경고를 띄우면 안 된다)
    gui, calls = _cookie_status_gui(monkeypatch, _result())
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: None)

    gui._refresh_cookie_status()

    assert calls[-1][1] != ft.Colors.RED
    assert calls[-1][0] == "저장된 쿠키: 없음"


def test_cookie_status_drops_the_warning_once_a_cookie_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: cred/Test-12b (재로그인하면 안내가 저절로 사라져야 한다)
    gui, calls = _cookie_status_gui(monkeypatch, _result(CookieMigration.LOST))
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: "NID_AUT=a")

    gui._refresh_cookie_status()

    assert calls[-1][1] == ft.Colors.GREEN


def test_build_runs_the_legacy_cookie_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: cred/Test-8
    # 이관 호출부를 gui.main()에서 _build()로 옮겼다. "여전히 불리는가"를 고정하지 않으면
    # 이 변경의 존재 이유를 지워도 스위트가 통과한다(실제로 뮤테이션이 생존했다).
    calls: list[int] = []

    def fake() -> CookieMigration:
        calls.append(1)
        return CookieMigration.NOTHING

    monkeypatch.setattr(gui_mod, "migrate_legacy_cookie", fake)

    gui = _bare_gui_with_build()

    assert calls == [1], "창을 만들 때 이관을 정확히 한 번 실행해야 한다"
    assert gui._migration is CookieMigration.NOTHING, "결과를 보관해야 화면에 반영할 수 있다"


def test_cookie_status_warns_about_a_leftover_plaintext_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b
    # 평문을 지우지 못하면 보관소 저장이 성공했더라도 자격증명이 디스크에 남는다.
    leftover = tmp_path / "cafe_cookie.txt"
    leftover.write_text("NID_AUT=a", encoding="utf-8")
    gui, calls = _cookie_status_gui(
        monkeypatch, _result(CookieMigration.MOVED, exposed=True, path=leftover)
    )
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: "NID_AUT=a")

    gui._refresh_cookie_status()

    assert calls[-1][1] == ft.Colors.RED
    assert "직접 삭제" in calls[-1][0]
    assert str(leftover) in calls[-1][0], "어느 파일을 지워야 하는지 알려야 한다"


def test_leftover_warning_clears_once_the_file_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b (사용자가 직접 지우면 경고가 저절로 걷혀야 한다)
    gui, calls = _cookie_status_gui(
        monkeypatch,
        _result(CookieMigration.MOVED, exposed=True, path=tmp_path / "already-deleted.txt"),
    )
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: "NID_AUT=a")

    gui._refresh_cookie_status()

    assert calls[-1][1] == ft.Colors.GREEN


def test_exposure_warning_survives_a_successful_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b
    # 사용자가 빨간 경고를 보고 '네이버 로그인'을 눌러 저장에 성공하면, 초록 성공 문구가
    # 경고를 덮어 문제가 해결됐다고 오해하게 된다. 평문은 그대로 디스크에 있다.
    leftover = tmp_path / "cafe_cookie.txt"
    leftover.write_text("NID_AUT=a", encoding="utf-8")
    gui, calls = _cookie_status_gui(
        monkeypatch, _result(CookieMigration.MOVED, exposed=True, path=leftover)
    )
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: "NID_AUT=a")

    gui._refresh_cookie_status("네이버 로그인 완료 — 쿠키를 저장했습니다. ✓")

    assert calls[-1][1] == ft.Colors.RED, "저장 성공 문구가 노출 경고를 덮으면 안 된다"


def test_exposure_and_loss_together_tell_the_user_to_log_in_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b
    # 노출과 손실이 겹치면, 안내대로 파일부터 지운 사용자는 다음 실행에서 이관될 수도
    # 있었던 **유일한 쿠키 사본**을 없앤다. 순서를 뒤집어 안내해야 한다.
    leftover = tmp_path / "cafe_cookie.txt"
    leftover.write_text("NID_AUT=a", encoding="utf-8")
    gui, calls = _cookie_status_gui(
        monkeypatch, _result(CookieMigration.LOST, exposed=True, path=leftover)
    )
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: None)

    gui._refresh_cookie_status()

    message = calls[-1][0]
    assert calls[-1][1] == ft.Colors.RED
    assert "네이버 로그인" in message, "로그인이 풀렸다는 사실이 사라지면 안 된다"
    assert message.index("네이버 로그인") < message.index("지워"), "재로그인이 삭제보다 먼저다"


def test_successful_save_routes_through_the_status_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b
    # 성공 문구를 _set_cookie_status로 직접 쓰면 노출 경고보다 뒤에 덮어써진다. 우선순위가
    # 한 곳(_refresh_cookie_status)에서만 결정되도록 배선 자체를 고정한다.
    gui = _bare_gui()
    gui.cookie_field = _FakeText()  # type: ignore[assignment]
    gui.cookie_field.page = None
    seen: list[str | None] = []
    monkeypatch.setattr(gui, "_refresh_cookie_status", lambda success=None: seen.append(success))
    monkeypatch.setattr(gui_mod, "parse_cookie_file", lambda _p: "NID_AUT=a")
    monkeypatch.setattr(gui_mod, "save_cookie", lambda *a, **kw: None)

    gui._update_cookie(tmp_path / "cookies.txt")

    assert seen and seen[0] is not None, "성공 문구도 상태 판정을 거쳐야 한다"


def test_successful_login_routes_through_the_status_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # covers: cred/Test-12b
    gui, _ = _bare_gui_with_run_thread_page()
    seen: list[str | None] = []
    monkeypatch.setattr(gui, "_refresh_cookie_status", lambda success=None: seen.append(success))
    monkeypatch.setattr(gui_mod, "login_and_capture", lambda *a, **kw: "NID_AUT=a")
    monkeypatch.setattr(gui_mod, "save_cookie", lambda *a, **kw: None)

    gui._run_cookie_login()

    assert seen and seen[0] is not None, "성공 문구도 상태 판정을 거쳐야 한다"


def test_stale_loss_notice_disappears_after_a_successful_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b
    # 노출+손실 상태에서 재로그인에 성공했는데도 "로그인도 풀렸습니다"가 그대로 나오면,
    # 이미 한 일을 다시 하라는 말이 되어 사용자는 로그인이 안 먹은 줄 안다.
    leftover = tmp_path / "cafe_cookie.txt"
    leftover.write_text("NID_AUT=a", encoding="utf-8")
    gui, calls = _cookie_status_gui(
        monkeypatch, _result(CookieMigration.LOST, exposed=True, path=leftover)
    )
    monkeypatch.setattr(gui_mod, "load_cookie", lambda: "NID_AUT=new")

    gui._refresh_cookie_status()

    message = calls[-1][0]
    assert calls[-1][1] == ft.Colors.RED, "평문은 아직 남아 있으므로 경고는 유지된다"
    assert "풀렸습니다" not in message, "방금 로그인했는데 로그인이 풀렸다고 하면 안 된다"
    assert str(leftover) in message, "지워야 할 파일은 계속 알려야 한다"
