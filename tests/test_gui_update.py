"""GUI 업데이트 계층(Velopack 전환) 검증.

커스텀 사이드카 업데이터를 Velopack 래퍼로 갈아 끼우면서 세 가지가 바뀐다.

1. 시작 워커가 **유지보수를 먼저** 부른다(오래된 nupkg를 지우는 지점이 거기뿐이다).
2. macOS에서도 자동 적용이 막히지 않는다(구 구현은 Windows 전용 사이드카였다).
3. 새 버전을 찾으면 버튼 라벨이 실제로 바뀐다 — flet 0.85의 ``ft.Button``에는 ``text``
   필드가 없어서 ``.text`` 대입은 아무 효과가 없었다(현재 살아 있는 버그).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from naver_post_crawler import gui as gui_mod
from naver_post_crawler.gui import CrawlerGUI


def _update_gui() -> CrawlerGUI:
    """업데이트 흐름이 건드리는 속성만 갖춘 인스턴스(``__init__`` 우회)."""
    gui = object.__new__(CrawlerGUI)
    gui.page = SimpleNamespace(run_thread=lambda fn: None, update=lambda: None)  # type: ignore[assignment]
    gui.update_btn = SimpleNamespace(content="업데이트 확인", icon=None, disabled=False, page=None)  # type: ignore[assignment]
    gui.update_status = SimpleNamespace(value="", color=None, page=None)  # type: ignore[assignment]
    gui._muted_color = None  # type: ignore[assignment]
    gui._pending_update = None  # type: ignore[assignment]
    gui._applying = False  # type: ignore[assignment]
    gui._set_update_status = lambda msg, color=None: None  # type: ignore[method-assign]
    return gui


def test_startup_worker_runs_maintenance_before_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-20
    order: list[str] = []
    monkeypatch.setattr(
        gui_mod.velopack_update, "run_startup_maintenance", lambda: order.append("maintenance")
    )
    gui = _update_gui()
    monkeypatch.setattr(
        gui_mod.CrawlerGUI, "_check_updates", lambda _self, manual: order.append("check")
    )

    gui._auto_check_updates()

    assert order == ["maintenance", "check"], (
        "오래된 nupkg 정리는 App().run()에서만 일어난다 — 확인보다 먼저 불러야 한다"
    )


def test_new_version_updates_button_content(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-21
    info = object()
    monkeypatch.setattr(gui_mod.velopack_update, "is_installed", lambda: True)
    monkeypatch.setattr(gui_mod.velopack_update, "check", lambda: info)
    monkeypatch.setattr(gui_mod.velopack_update, "target_version", lambda _i: "0.2.0")
    gui = _update_gui()

    gui._check_updates(manual=True)

    assert gui._pending_update is info
    assert "0.2.0" in gui.update_btn.content, (
        "ft.Button에는 text 필드가 없다 — .content를 갱신해야 라벨이 실제로 바뀐다"
    )


def test_apply_path_is_not_blocked_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-22
    calls: list[str] = []
    monkeypatch.setattr(gui_mod.sys, "platform", "darwin")
    monkeypatch.setattr(gui_mod.velopack_update, "is_installed", lambda: True)
    monkeypatch.setattr(
        gui_mod.velopack_update, "download", lambda _i, _cb=None: calls.append("download")
    )
    monkeypatch.setattr(
        gui_mod.velopack_update, "apply_and_restart", lambda _i: calls.append("apply")
    )
    monkeypatch.setattr(gui_mod.time, "sleep", lambda _s: None)
    gui = _update_gui()
    gui._pending_update = object()  # type: ignore[assignment]

    gui._download_and_apply()

    assert calls == ["download", "apply"]


def test_non_installed_context_does_not_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-23
    calls: list[str] = []
    messages: list[str] = []
    monkeypatch.setattr(gui_mod.velopack_update, "is_installed", lambda: False)
    monkeypatch.setattr(
        gui_mod.velopack_update, "download", lambda _i, _cb=None: calls.append("download")
    )
    monkeypatch.setattr(
        gui_mod.velopack_update, "apply_and_restart", lambda _i: calls.append("apply")
    )
    gui = _update_gui()
    gui._pending_update = object()  # type: ignore[assignment]
    gui._set_update_status = lambda msg, color=None: messages.append(msg)  # type: ignore[method-assign]

    gui._download_and_apply()

    assert calls == []
    assert messages, "왜 적용하지 않는지 사용자에게 알려야 한다"
    assert gui._applying is False, "재시도할 수 있게 가드가 풀려야 한다"


def test_reclick_while_applying_starts_no_worker() -> None:
    # covers: Test-24
    started: list[object] = []
    gui = _update_gui()
    gui.page = SimpleNamespace(run_thread=started.append, update=lambda: None)  # type: ignore[assignment]
    gui._pending_update = object()  # type: ignore[assignment]
    gui._applying = True  # type: ignore[assignment]

    gui._on_update_click()

    assert started == []


def test_dev_run_does_not_attempt_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-23
    # 실측 버그: 개발 실행(uv run python scripts/run.py)에서 velopack UpdateManager 생성이
    # RuntimeError("This application is not properly installed")로 죽어 화면에
    # "업데이트 확인 실패 + 트레이스백"이 떴다. 설치본이 아니면 확인 자체를 하지 않는다.
    messages: list[str] = []
    monkeypatch.setattr(gui_mod.velopack_update, "is_installed", lambda: False)

    def boom():
        raise RuntimeError("This application is not properly installed")

    monkeypatch.setattr(gui_mod.velopack_update, "check", boom)
    gui = _update_gui()
    gui._set_update_status = lambda msg, color=None: messages.append(msg)  # type: ignore[method-assign]

    gui._check_updates(manual=True)

    assert gui._pending_update is None
    assert messages, "왜 확인하지 않는지 알려야 한다"
    assert not any("실패" in m for m in messages), f"실패로 보이면 안 된다: {messages}"


def test_installed_run_still_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-23 (가드가 설치본까지 막아버리면 자동 업데이트가 죽는다)
    monkeypatch.setattr(gui_mod.velopack_update, "is_installed", lambda: True)
    monkeypatch.setattr(gui_mod.velopack_update, "check", lambda: None)
    calls: list[str] = []
    monkeypatch.setattr(
        gui_mod.velopack_update, "target_version", lambda _i: calls.append("v") or "0.2.0"
    )
    gui = _update_gui()

    gui._check_updates(manual=True)  # 예외 없이 "최신" 경로로 끝나야 한다
