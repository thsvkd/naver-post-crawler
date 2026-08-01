"""Windows 제거 훅 — 자격 증명 관리자 항목 정리 검증.

Velopack은 제거할 때 앱 exe를 ``--veloapp-uninstall``로 실행하고 종료를 기다린다. flet의
Dart 진입점은 인자가 하나라도 있으면 개발자 모드로 보고 파이썬을 실행하지 않으므로,
훅에서 할 일은 **네이티브 진입점**에서 해야 한다(``scripts/flet_template.py``).

여기서 지우는 것은 **자격 증명 항목 하나뿐**이다. 데이터 *폴더* 삭제로 확장하지 않는다 —
C++이 경로를 알아야 해서 하드코딩이 되고, 틀리면 엉뚱한 폴더를 지운다. 자격증명이 보관소로
빠지고 나면 남는 것은 설정·로그뿐이라 지울 이유도 없다.

macOS에는 제거 훅이 존재하지 않는다(``.pkg``는 언인스톨러를 만들지 않는다). 키체인 항목은
남으며 README가 수동 정리를 안내한다.

번호는 `docs/handoff-credential-storage.md`의 인수 기준을 따른다.
"""

from __future__ import annotations

import re

import flet_template
import pytest

from naver_post_crawler import credentials

_WIDTH, _HEIGHT = 760, 720

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

# 제거가 아닌 훅. 여기서 자격증명을 지우면 매 업데이트마다 로그아웃된다.
_NON_UNINSTALL_HOOKS = ("--veloapp-install", "--veloapp-updated", "--veloapp-obsolete")


def _patched() -> str:
    return flet_template.patch_windows_runner(_TEMPLATE_MAIN_CPP, width=_WIDTH, height=_HEIGHT)


def _uninstall_block(source: str) -> str:
    """``--veloapp-uninstall`` 조건문의 본문을 중괄호 균형으로 잘라낸다.

    "지우는 코드가 제거 훅 안에만 있다"를 문자열 순서가 아니라 **구조**로 확인하려면
    블록 경계를 실제로 계산해야 한다.
    """
    # 조건식 안에 ::wcsstr(...)의 괄호가 중첩되므로 한 줄 안에서 탐욕적으로 잡는다.
    match = re.search(r"if\s*\(.*--veloapp-uninstall.*\)\s*\{", source)
    assert match is not None, "--veloapp-uninstall 전용 조건문을 찾지 못했다"
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("조건문 블록이 닫히지 않았다")


def test_uninstall_hook_deletes_the_stored_credential() -> None:
    # covers: Test-13
    block = _uninstall_block(_patched())

    for target in credentials.WINDOWS_TARGETS:
        assert f'L"{target}"' in block, f"제거 훅이 {target} 항목을 지우지 않는다"
    assert "CredDeleteW" in block


def test_credential_deletion_lives_only_inside_the_uninstall_branch() -> None:
    # covers: Test-14 (install/updated/obsolete에서 지우면 매 업데이트마다 로그아웃된다)
    patched = _patched()
    block = _uninstall_block(patched)

    # 주석·pragma의 언급이 아니라 **호출부**만 센다.
    assert patched.count("::CredDeleteW(") == block.count("::CredDeleteW("), (
        "CredDeleteW 호출이 제거 훅 블록 밖에도 있다"
    )
    assert block.count("::CredDeleteW(") == len(credentials.WINDOWS_TARGETS)


@pytest.mark.parametrize("hook_arg", _NON_UNINSTALL_HOOKS)
def test_non_uninstall_hooks_still_exit_without_deleting(hook_arg: str) -> None:
    # covers: Test-14
    patched = _patched()
    condition = re.search(r"if\s*\(.*--veloapp-uninstall.*\)", patched)
    assert condition is not None
    needles = re.findall(r'L"(--veloapp-[a-z-]*)"', condition.group(0))

    assert needles, "제거 판정 문자열을 찾지 못했다"
    assert not any(needle in hook_arg for needle in needles), (
        f"{hook_arg}가 제거 분기에 걸린다(조건: {needles})"
    )


def test_hook_exits_successfully_regardless_of_deletion_result() -> None:
    # covers: Test-15
    # 항목이 없으면 CredDeleteW는 실패를 돌려준다. 그걸 이유로 종료 코드를 바꾸면
    # Velopack이 "설치가 부분적으로 성공했습니다"를 띄운다.
    patched = _patched()
    block = _uninstall_block(patched)

    assert "return EXIT_FAILURE" not in block, "삭제 실패로 훅이 실패 종료하면 안 된다"
    hook_index = patched.index("--veloapp-")
    assert patched.index("return EXIT_SUCCESS;", hook_index) < patched.index("::CoInitializeEx")


def test_credential_api_is_declared_and_linked() -> None:
    # covers: Test-13 (헤더·라이브러리가 빠지면 컴파일/링크 단계에서만 드러난다)
    patched = _patched()

    assert "#include <wincred.h>" in patched
    assert 'comment(lib, "advapi32.lib")' in patched, "CredDeleteW는 advapi32에 있다"


def test_windows_targets_match_what_keyring_actually_writes() -> None:
    # covers: Test-13
    # C++ 훅은 keyring이 만든 항목 이름을 그대로 알아야 한다. 이 대응이 어긋나면
    # 훅은 조용히 아무것도 못 지우고, 제거 후에도 자격증명이 남는다.
    from keyring.backends.Windows import WinVaultKeyring

    compound = WinVaultKeyring._compound_name(credentials.ACCOUNT, credentials.SERVICE)

    assert credentials.SERVICE in credentials.WINDOWS_TARGETS
    assert compound in credentials.WINDOWS_TARGETS
