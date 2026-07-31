#!/usr/bin/env python3
"""네이티브 러너(Windows, Flutter) 진입점 패치 — flet 빌드 템플릿을 고쳐서 쓴다.

``flet build``는 flet이 배포하는 cookiecutter 템플릿으로 Flutter 앱 껍데기를 만든 뒤
빌드한다. 그 껍데기의 Windows 진입점(``windows/runner/main.cpp``)에 아래 두 가지가 빠져
있어서, 파이썬 코드로는 고칠 수 없는 증상이 배포본에서 나타난다. flet은
``--template <디렉터리>``로 템플릿을 바꿔 끼울 수 있으므로, 공식 템플릿을 그대로 내려받아
이 두 곳만 패치한 사본을 빌드에 넘긴다.

1) 설치기가 "설치가 부분적으로 성공했습니다" 경고를 띄운다.
   Velopack은 설치/업데이트/제거 때 앱 exe를 훅 인자(``--veloapp-install`` 등)와 함께
   실행하고 끝나기를 기다린다(안 끝나면 죽이고 위 경고를 띄운다). 그런데 flet이 만든 Dart
   진입점은 **명령행 인자가 하나라도 있으면 "개발자 모드"** 로 간주해 그 인자를 페이지
   URL로 해석하고 파이썬을 아예 실행하지 않는다. 그래서 앱 쪽에서 훅을 처리할 기회 자체가
   없고, 훅은 항상 타임아웃한다.
   → 네이티브 진입점에서 훅 인자를 보면 Flutter 엔진을 띄우기 전에 그대로 성공 종료한다.
   훅에서 우리가 할 일은 없다(자동 업데이트는 앱 안의 ``UpdateManager``가 따로 한다).

2) 처음 뜰 때 창 크기가 한 번 바뀐다.
   러너는 창을 1280x720으로 만들고 Flutter가 첫 프레임을 그리는 순간 그대로 보여준다. 그
   첫 프레임은 파이썬이 붙기 한참 전이라, 창은 1280x720으로 먼저 보이고 파이썬이 붙고
   나서야 앱 크기로 줄어든다.
   → 처음부터 앱의 기본 창 크기로 만들게 한다. 크기의 SSOT는 ``src/naver_post_crawler/gui.py``다.

macOS 러너는 패치하지 않는다. ``--veloapp-*`` 인자는 Velopack의 Windows 설치 경로에서만
만들어지고, macOS는 ``.pkg``의 postinstall이 인자 없이 앱을 띄우므로 1)번 문제가 없다.
2)번(첫 창 크기)은 macOS에도 있지만 창 정의가 코드가 아니라 ``MainMenu.xib``에 있어 별도
패치가 필요하고, 이번 이관의 합의된 완료 범위 밖이다.

앵커 문자열이 정확히 한 번 나오지 않으면(= flet이 템플릿을 바꿨으면) 조용히 넘어가지 않고
빌드를 실패시킨다. 패치가 사라진 채 배포되면 위 증상이 그대로 돌아오기 때문이다.
"""

from __future__ import annotations

import re
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from _common import REPO_ROOT, fail, info

# flet이 릴리스마다 올리는 공식 빌드 템플릿(zip). flet_cli가 쓰는 것과 같은 파일이라
# 버전만 맞추면 기본 빌드와 동일한 결과가 나온다.
_TEMPLATE_URL = (
    "https://github.com/flet-dev/flet/releases/download/v{version}/flet-build-template.zip"
)
# zip 최상위는 cookiecutter.json이 들어 있는 디렉터리 하나(build/)다. `--template`에는
# 이 디렉터리를 넘겨야 한다.
_TEMPLATE_ROOT = "build"
# 템플릿 안에서 패치할 파일(경로에 cookiecutter 변수명이 그대로 들어간다).
_RUNNER_MAIN = Path("{{cookiecutter.out_dir}}") / "windows" / "runner" / "main.cpp"

# 패치 내용이 바뀌면 이 값을 올린다. 이 값은 **캐시 디렉터리 이름에 들어간다** — 그래야
# 한다. flet build는 템플릿의 내용이 아니라 경로/버전만 해시해 Flutter 프로젝트 재생성
# 여부를 정하므로, 경로가 그대로면 패치를 고쳐도 build/flutter를 재사용해 **옛 main.cpp로
# 조용히 빌드된다**. 경로가 달라지면 flet의 해시도 달라져 반드시 다시 생성한다.
_PATCH_REVISION = 1

# -- 패치 정의 ---------------------------------------------------------------
# 주석을 영어로 쓰는 이유: MSVC는 BOM 없는 UTF-8 소스의 비ASCII 문자에 C4819 경고를 낸다.
_INCLUDE_ANCHOR = "#include <windows.h>\n"
_INCLUDE_PATCH = "#include <windows.h>\n#include <wchar.h>\n"

_HOOK_ANCHOR = "_In_ wchar_t *command_line, _In_ int show_command) {\n"
_HOOK_PATCH = (
    _HOOK_ANCHOR
    + """\
  // [npc] Velopack lifecycle hooks (--veloapp-install/-updated/-obsolete/
  // -uninstall). The installer runs this exe with one of those arguments and
  // waits for it to exit; otherwise it kills it and warns the user that the
  // installation only partially succeeded. Flet's Dart entrypoint treats any
  // command line argument as "developer mode" and never starts Python, so the
  // hook can not be handled on the Python side at all. There is nothing to do
  // for these hooks, so exit before COM and the Flutter engine start.
  if (command_line != nullptr && ::wcsstr(command_line, L"--veloapp-") != nullptr) {
    return EXIT_SUCCESS;
  }
"""
)

_SIZE_ANCHOR = "  Win32Window::Size size(1280, 720);"
_SIZE_PATCH = "  Win32Window::Size size({width}, {height});"

# GUI 기본 창 크기의 SSOT. 파이썬 모듈을 import하면 flet까지 딸려 오므로 소스에서 읽는다.
_GUI_PATH = REPO_ROOT / "src" / "naver_post_crawler" / "gui.py"
_SIZE_RE = re.compile(r"^_WINDOW_(WIDTH|HEIGHT)\s*=\s*(\d+)\b", re.MULTILINE)


def window_size() -> tuple[int, int]:
    """``src/naver_post_crawler/gui.py``가 정의한 기본 창 크기 ``(가로, 세로)``.

    파이썬이 나중에 지정하는 크기와 네이티브 러너가 만드는 첫 창 크기가 같아야 시작 시
    창이 한 번 바뀌는 깜빡임이 없다. 두 값을 한 곳에서만 정의하려고 빌드 시점에 읽는다.
    """
    found = dict(_SIZE_RE.findall(_GUI_PATH.read_text(encoding="utf-8")))
    if "WIDTH" not in found or "HEIGHT" not in found:
        fail(f"{_GUI_PATH}에서 _WINDOW_WIDTH/_WINDOW_HEIGHT를 찾지 못했습니다.")
    return int(found["WIDTH"]), int(found["HEIGHT"])


def _replace_once(text: str, anchor: str, replacement: str, what: str) -> str:
    """``anchor``가 정확히 한 번 나올 때만 치환한다. 아니면 :class:`ValueError`."""
    count = text.count(anchor)
    if count != 1:
        raise ValueError(
            f"'{what}' 패치 실패 — 기준 문자열이 {count}번 나왔습니다(1번이어야 함). "
            "flet 버전이 올라가며 템플릿이 바뀐 것 같습니다. "
            "scripts/flet_template.py의 앵커를 새 템플릿에 맞게 고치세요.\n"
            f"  기준 문자열: {anchor!r}"
        )
    return text.replace(anchor, replacement)


def patch_windows_runner(text: str, *, width: int, height: int) -> str:
    """Windows 러너 진입점(``main.cpp``) 소스에 두 패치를 적용한 결과를 돌려준다.

    훅 조기 종료는 ``::CoInitializeEx``보다 앞에 심는다. COM 초기화 뒤에서 return하면
    ``::CoUninitialize``를 건너뛰어 초기화/해제 짝이 깨진다.

    Raises:
        ValueError: 기준 문자열이 정확히 한 번 나오지 않을 때(템플릿 구조 변경).
    """
    text = _replace_once(text, _INCLUDE_ANCHOR, _INCLUDE_PATCH, "wchar.h 포함")
    text = _replace_once(text, _HOOK_ANCHOR, _HOOK_PATCH, "Velopack 훅 조기 종료")
    text = _replace_once(
        text, _SIZE_ANCHOR, _SIZE_PATCH.format(width=width, height=height), "기본 창 크기"
    )
    return text


def cache_dir_name(flet_version: str, *, width: int, height: int) -> str:
    """패치된 템플릿을 캐시할 디렉터리 이름.

    flet 버전뿐 아니라 **패치 리비전과 창 크기까지** 이름에 넣는다. flet은 템플릿의 내용이
    아니라 경로/버전만 해시해 Flutter 프로젝트 재생성 여부를 정하므로, 경로가 그대로면
    패치를 고쳐도 옛 결과물로 조용히 빌드된다(:data:`_PATCH_REVISION` 주석 참고).
    """
    return f"{flet_version}-r{_PATCH_REVISION}-{width}x{height}"


def cache_stamp_key(*, width: int, height: int) -> str:
    """캐시 디렉터리 안에 남기는 스탬프 값.

    :func:`cache_dir_name`과 **같은 입력에 함께 반응해야 한다**. 한쪽만 반응하면 캐시가
    "맞다"고 판정해 패치가 빠진 템플릿을 그대로 재사용한다.
    """
    return f"{_PATCH_REVISION}|{width}x{height}"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    info(f"flet 빌드 템플릿 내려받는 중… {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 - 고정된 https 릴리스 URL
    except (urllib.error.URLError, OSError) as exc:
        fail(f"flet 빌드 템플릿을 받지 못했습니다({url}): {exc}")
    tmp.replace(dest)


def prepare(flet_version: str) -> Path:
    """패치된 flet 빌드 템플릿 디렉터리를 준비해 경로를 돌려준다.

    ``flet build --template <반환값>``으로 넘긴다. 결과물은 ``build/`` 아래(빌드 산출물,
    git 무시)에 flet 버전별로 캐시되며, 패치 내용이나 창 크기가 바뀌면 다시 만든다.
    Windows 빌드에서만 쓴다(모듈 docstring 참고).
    """
    width, height = window_size()
    base = REPO_ROOT / "build" / "_flet_template"
    root = base / cache_dir_name(flet_version, width=width, height=height)
    template_dir = root / _TEMPLATE_ROOT
    stamp = root / ".npc-patch"
    key = cache_stamp_key(width=width, height=height)

    if (
        template_dir.is_dir()
        and stamp.is_file()
        and stamp.read_text(encoding="utf-8").strip() == key
    ):
        info(f"패치된 flet 템플릿 재사용: {template_dir}")
        return template_dir

    archive = base / f"flet-build-template-{flet_version}.zip"
    if not archive.is_file():
        _download(_TEMPLATE_URL.format(version=flet_version), archive)

    if root.is_dir():
        shutil.rmtree(root)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(root)

    main_cpp = root / _TEMPLATE_ROOT / _RUNNER_MAIN
    if not main_cpp.is_file():
        fail(f"템플릿에서 Windows 러너 진입점을 찾지 못했습니다: {main_cpp}")
    try:
        patched = patch_windows_runner(
            main_cpp.read_text(encoding="utf-8"), width=width, height=height
        )
    except ValueError as exc:
        fail(str(exc))
    main_cpp.write_text(patched, encoding="utf-8")

    stamp.write_text(key, encoding="utf-8")
    info(f"Windows 러너 패치 완료(Velopack 훅 처리 + 첫 창 {width}x{height}): {template_dir}")
    return template_dir
