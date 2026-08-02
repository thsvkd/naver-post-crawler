"""Windows 네이티브 러너 패치(scripts/flet_template.py) 검증.

``flet build``는 flet이 배포하는 cookiecutter 템플릿으로 Flutter 앱 껍데기를 만든 뒤
빌드한다. 그 껍데기의 Windows 진입점(``windows/runner/main.cpp``)에 두 가지가 빠져 있어
파이썬 코드로는 고칠 수 없는 증상이 배포본에서 나타난다.

1. Velopack이 설치/업데이트/제거 때 앱 exe를 훅 인자(``--veloapp-*``)와 함께 실행하는데,
   flet의 Dart 진입점이 **인자가 하나라도 있으면 개발자 모드**로 보고 파이썬을 실행하지
   않는다 → 훅이 항상 타임아웃하고 설치기가 "설치가 부분적으로 성공했습니다"를 띄운다.
2. 러너가 창을 1280x720으로 먼저 만들어 보여준 뒤 파이썬이 붙고 나서야 앱 크기로 줄어든다
   → 시작할 때 창 크기가 한 번 바뀌는 깜빡임.

둘 다 빌드해서 설치해 보기 전에는 알아채기 어렵고, macOS 개발 머신에서는 재현되지
않는다. 그래서 패치 적용 여부와 앵커가 어긋났을 때 소리 내어 실패하는지를 여기서 잡는다.
macOS 러너는 패치하지 않는다(핸드오프 W-8) — ``.pkg``의 postinstall이 인자 없이 앱을
띄우므로 훅 문제가 없고, 창 크기는 C++ 한 줄이 아니라 XIB 패치가 필요하다.
"""

from __future__ import annotations

import re

import flet_template
import pytest

from naver_post_crawler import gui as gui_mod

# flet 0.85 빌드 템플릿의 windows/runner/main.cpp에서 패치와 관련된 부분만 발췌한 것.
_TEMPLATE_MAIN_CPP = """\
#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <windows.h>

#include "flutter_window.h"
#include "utils.h"

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  flutter::DartProject project(L"data");

  FlutterWindow window(project);
  Win32Window::Point origin(10, 10);
  Win32Window::Size size(1280, 720);
  if (!window.Create(L"{{ cookiecutter.product_name }}", origin, size)) {
    return EXIT_FAILURE;
  }

  ::CoUninitialize();
  return EXIT_SUCCESS;
}
"""

# Velopack이 앱 exe에 넘기는 라이프사이클 훅 인자 전부.
_HOOK_ARGS = (
    "--veloapp-install",
    "--veloapp-updated",
    "--veloapp-obsolete",
    "--veloapp-uninstall",
)

# 패치가 심는 ``::wcsstr(command_line, L"...")``에서 실제 비교 문자열을 뽑는다.
# wcsstr은 부분 문자열 검색이므로, 이 문자열이 훅 인자에 들어 있으면 조기 종료 경로다.
_NEEDLE_RE = re.compile(r'::wcsstr\(\s*command_line\s*,\s*L"([^"]+)"\s*\)')

_WIDTH, _HEIGHT = 760, 720


def _patched() -> str:
    """GUI 상수(SSOT)와 같은 크기로 패치한 main.cpp."""
    return flet_template.patch_windows_runner(_TEMPLATE_MAIN_CPP, width=_WIDTH, height=_HEIGHT)


def test_hook_check_precedes_window_creation() -> None:
    # covers: Test-3
    patched = _patched()
    assert '::wcsstr(command_line, L"--veloapp-")' in patched
    assert patched.index("--veloapp-") < patched.index("FlutterWindow window(project)"), (
        "훅 처리는 Flutter 엔진(창 생성)보다 먼저 있어야 한다"
    )


def test_hook_return_precedes_com_initialization() -> None:
    # covers: Test-4
    patched = _patched()
    # CoInitializeEx 뒤에서 return하면 CoUninitialize 없이 종료해 초기화/해제 짝이 깨진다.
    hook_index = patched.index("--veloapp-")
    return_index = patched.index("return EXIT_SUCCESS;", hook_index)
    assert return_index < patched.index("::CoInitializeEx"), (
        "훅 조기 return은 ::CoInitializeEx보다 앞에 있어야 한다"
    )


@pytest.mark.parametrize("hook_arg", _HOOK_ARGS)
def test_every_hook_argument_matches_early_exit(hook_arg: str) -> None:
    # covers: Test-5
    patched = _patched()
    needles = _NEEDLE_RE.findall(patched)
    assert needles, "훅 판정 조건(::wcsstr)을 찾지 못했다"
    assert any(needle in hook_arg for needle in needles), (
        f"{hook_arg}가 조기 종료 조건에 걸리지 않는다(조건: {needles})"
    )


def test_declares_wchar_header_and_matches_gui_window_size() -> None:
    # covers: Test-6
    patched = _patched()
    assert "#include <wchar.h>" in patched  # ::wcsstr 선언
    assert f"Win32Window::Size size({_WIDTH}, {_HEIGHT});" in patched
    assert "1280, 720" not in patched


def test_window_size_comes_from_gui_constants() -> None:
    # covers: Test-6 (첫 창 크기의 SSOT는 gui.py 모듈 상수다)
    assert flet_template.window_size() == (gui_mod._WINDOW_WIDTH, gui_mod._WINDOW_HEIGHT)
    assert flet_template.window_size() == (_WIDTH, _HEIGHT)


def test_rest_of_template_is_untouched() -> None:
    # covers: Test-6 (패치는 추가/치환만 한다 — 원본의 다른 줄이 사라지면 안 된다)
    patched = _patched()
    for line in ("FlutterWindow window(project);", "return EXIT_FAILURE;", "::CoUninitialize();"):
        assert line in patched


def test_fails_loudly_when_anchor_is_missing() -> None:
    # covers: Test-7
    changed = _TEMPLATE_MAIN_CPP.replace(
        "Win32Window::Size size(1280, 720);", "Win32Window::Size size(1024, 768);"
    )
    with pytest.raises(ValueError):
        flet_template.patch_windows_runner(changed, width=_WIDTH, height=_HEIGHT)


def test_fails_loudly_when_anchor_appears_twice() -> None:
    # covers: Test-7 (앵커가 여러 번이면 어느 쪽을 고칠지 알 수 없다 — 조용히 넘어가지 않는다)
    changed = _TEMPLATE_MAIN_CPP.replace(
        "  Win32Window::Size size(1280, 720);",
        "  Win32Window::Size size(1280, 720);\n  Win32Window::Size size(1280, 720);",
    )
    with pytest.raises(ValueError):
        flet_template.patch_windows_runner(changed, width=_WIDTH, height=_HEIGHT)


def test_is_idempotent_guard() -> None:
    # covers: Test-7 (이미 패치된 소스에 재적용하면 앵커가 사라져 실패해야 한다)
    patched = _patched()
    with pytest.raises(ValueError):
        flet_template.patch_windows_runner(patched, width=_WIDTH, height=_HEIGHT)


def test_cache_dir_name_encodes_version_revision_and_size() -> None:
    # covers: Test-8
    # flet build는 템플릿의 '내용'이 아니라 경로/버전만 해시해 Flutter 프로젝트 재생성
    # 여부를 정한다. 패치 리비전·창 크기가 경로에 없으면 패치를 고쳐도 옛 main.cpp로
    # 조용히 빌드된다.
    name = flet_template.cache_dir_name("0.85.1", width=_WIDTH, height=_HEIGHT)

    assert "0.85.1" in name
    assert f"r{flet_template._PATCH_REVISION}" in name
    assert f"{_WIDTH}x{_HEIGHT}" in name


def test_cache_dir_name_differs_when_any_input_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-8
    base = flet_template.cache_dir_name("0.85.1", width=_WIDTH, height=_HEIGHT)

    assert base != flet_template.cache_dir_name("0.86.0", width=_WIDTH, height=_HEIGHT)
    assert base != flet_template.cache_dir_name("0.85.1", width=900, height=_HEIGHT)
    assert base != flet_template.cache_dir_name("0.85.1", width=_WIDTH, height=800)

    monkeypatch.setattr(flet_template, "_PATCH_REVISION", flet_template._PATCH_REVISION + 1)
    assert base != flet_template.cache_dir_name("0.85.1", width=_WIDTH, height=_HEIGHT)


def test_cache_stamp_key_moves_with_cache_dir_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-8 (스탬프 키와 디렉터리 이름이 같은 입력에 함께 반응해야 한다 —
    # 한쪽만 반응하면 캐시가 '맞다'고 판정해 패치가 빠진 채 재사용된다)
    key = flet_template.cache_stamp_key(width=_WIDTH, height=_HEIGHT)

    assert key != flet_template.cache_stamp_key(width=900, height=_HEIGHT)
    assert key != flet_template.cache_stamp_key(width=_WIDTH, height=800)

    monkeypatch.setattr(flet_template, "_PATCH_REVISION", flet_template._PATCH_REVISION + 1)
    assert key != flet_template.cache_stamp_key(width=_WIDTH, height=_HEIGHT)


def test_cache_key_moves_when_credential_target_names_change() -> None:
    """자격 증명 항목 이름이 바뀌면 캐시 키도 바뀌어야 한다.

    이름은 제거 훅의 ``CredDeleteW`` 인자로 main.cpp에 박힌다. credentials.py의
    SERVICE/ACCOUNT를 바꾸면서 _PATCH_REVISION 올리는 걸 잊으면, 캐시가 "맞다"고 판정해
    **옛 이름을 지우는 제거 훅이 그대로 배포된다**. 그건 아무것도 지우지 않고 조용히
    성공하므로, 실기로 제거해 보기 전엔 드러나지 않는다.
    """
    other = ("other-service", "other-account@other-service")

    assert flet_template.cache_dir_name(
        "0.85.1", width=_WIDTH, height=_HEIGHT
    ) != flet_template.cache_dir_name("0.85.1", width=_WIDTH, height=_HEIGHT, targets=other)
    assert flet_template.cache_stamp_key(
        width=_WIDTH, height=_HEIGHT
    ) != flet_template.cache_stamp_key(width=_WIDTH, height=_HEIGHT, targets=other)


# -- macOS 러너(MainMenu.xib) 패치 -------------------------------------------------------
# flet 0.85 빌드 템플릿의 macos/Runner/Base.lproj/MainMenu.xib에서 패치 관련 부분만 발췌.
# 여기서 재현하려는 함정: `width="800" height="600"`이 **두 곳**(창의 contentRect,
# contentView의 frame)에 똑같이 나온다. 그 부분 문자열만으로 앵커를 잡으면 두 번 매치되어
# 패치가 엉뚱한 줄을 건드리거나 _replace_once가 빌드를 죽인다. 그래서 앵커에 `key=`와
# 좌표까지 넣어 줄 단위로 유일하게 만든다 — 이 픽스처가 그 유일성을 고정한다.
_TEMPLATE_MAIN_MENU_XIB = """\
<?xml version="1.0" encoding="UTF-8"?>
<document type="com.apple.InterfaceBuilder3.Cocoa.XIB" version="3.0">
    <objects>
        <window title="APP" id="QvC-M9-y7g" customClass="MainFlutterWindow">
            <rect key="contentRect" x="335" y="390" width="800" height="600"/>
            <rect key="screenRect" x="0.0" y="0.0" width="2560" height="1577"/>
            <view key="contentView" wantsLayer="YES" id="EiT-Mj-1SZ">
                <rect key="frame" x="0.0" y="0.0" width="800" height="600"/>
            </view>
        </window>
    </objects>
</document>
"""


def test_naive_size_anchor_would_be_ambiguous() -> None:
    """앵커를 좌표 없이 크기만으로 잡으면 안 되는 이유를 픽스처가 실제로 담고 있는지."""
    assert _TEMPLATE_MAIN_MENU_XIB.count('width="800" height="600"') == 2


def test_macos_runner_patch_sets_both_rects() -> None:
    """창(contentRect)과 컨텐트 뷰(frame)를 둘 다 고쳐야 처음부터 앱 크기로 뜬다.

    ``MainFlutterWindow.awakeFromNib()``이 ``self.frame``을 그대로 다시 세팅하므로
    한쪽만 고치면 창이 여전히 800x600으로 먼저 보인다.
    """
    patched = flet_template.patch_macos_runner(
        _TEMPLATE_MAIN_MENU_XIB, width=_WIDTH, height=_HEIGHT
    )

    assert f'key="contentRect" x="335" y="390" width="{_WIDTH}" height="{_HEIGHT}"' in patched
    assert f'key="frame" x="0.0" y="0.0" width="{_WIDTH}" height="{_HEIGHT}"' in patched
    # 화면 크기(screenRect)와 디코이는 건드리면 안 된다.
    assert 'key="screenRect" x="0.0" y="0.0" width="2560" height="1577"' in patched
    assert patched.count(f'width="{_WIDTH}" height="{_HEIGHT}"') == 2


def test_macos_runner_patch_fails_when_anchor_is_missing() -> None:
    """flet이 xib 구조를 바꾸면 조용히 넘어가지 말고 빌드를 세워야 한다."""
    changed = _TEMPLATE_MAIN_MENU_XIB.replace('key="contentRect"', 'key="contentFrame"')

    with pytest.raises(ValueError):
        flet_template.patch_macos_runner(changed, width=_WIDTH, height=_HEIGHT)


def test_macos_runner_patch_is_not_idempotent_by_design() -> None:
    """이미 패치된 xib를 다시 패치하면 실패해야 한다(앵커가 사라졌다는 뜻)."""
    patched = flet_template.patch_macos_runner(
        _TEMPLATE_MAIN_MENU_XIB, width=_WIDTH, height=_HEIGHT
    )

    with pytest.raises(ValueError):
        flet_template.patch_macos_runner(patched, width=_WIDTH, height=_HEIGHT)
