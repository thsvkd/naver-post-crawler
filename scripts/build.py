#!/usr/bin/env python3
"""flet 네이티브 앱 빌드 스크립트. 실행한 OS를 감지해 빌드한다.

자동 처리:
  - Windows: Visual Studio "Desktop development with C++" 워크로드가 없으면 설치한다.
  - macOS:   Xcode Command Line Tools를 확인하고 CocoaPods를 Homebrew로 설치한다.
  - Linux:   clang/cmake/ninja/pkg-config/GTK3 개발 패키지를 패키지 매니저로 설치한다.
             (root가 아니면 sudo를 쓰며, sudo가 없으면 설치를 건너뛰고 안내한다.)
  - Flutter SDK는 flet build가 필요 시 자동으로 내려받는다.

사용:
    python scripts/build.py

결과물: build/<platform>/ 아래 앱 번들.
(개발 중 빠른 실행은 'python scripts/run.py --gui')

앱 데이터 저장 위치: 데스크톱 빌드는 실행 파일과 같은 폴더의 storage/ 에 저장한다
(flet 기본값인 <Documents>/flet/<app> 대신). 앱을 폴더째 배포하면 어디서 실행하든
자기 폴더 안에 데이터를 두는 포터블 동작이며, OneDrive로 옮겨진 Documents 등에
의존하지 않는다. 단, 쓰기 가능한 위치에서 실행해야 한다(예: Program Files 아래 X).

참고: Flutter/네이티브 툴체인 설치가 부담되면 flet build 대신 PyInstaller 기반
      'uv run flet pack src/naver_blog_crawler/gui.py' 로 단일 실행파일을 만들 수 있다.
      (백신 오탐·큰 용량 등 단점이 있어 배포보다 임시 실행용 폴백으로 권장.)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

from _common import REPO_ROOT, check, fail, info, require_uv

# flet build 메타데이터(두 플랫폼 빌드가 동일하게 쓴다).
_PRODUCT = "Naver Blog Backup"
_ORG = "com.thsvkd"

# Visual Studio C++ 빌드 도구 워크로드(컴포넌트) 식별자.
_VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"

# flet 네이티브 앱은 기본적으로 데이터 폴더를 <Documents>/flet/<app>에 만든다
# (생성 코드는 빌드 템플릿의 lib/main.dart). 그런데 Documents가 OneDrive로 옮겨져
# 쓰기 불능이면 시작 시 폴더 생성에 실패해 앱이 죽고, 폴더째 배포하는 포터블 앱에도
# 적합하지 않다. 그래서 빌드 템플릿의 main.dart를 받아 "데스크톱에서는 실행 파일 옆
# storage/ 폴더에 저장"하도록 패치한 뒤 그 템플릿(--template)으로 빌드한다.
_FLET_TEMPLATE_REF = "v0.85.1"  # 설치된 flet 버전에 맞춘 빌드 템플릿 태그
_FLET_TEMPLATE_URL = (
    "https://github.com/flet-dev/flet/releases/download/"
    f"{_FLET_TEMPLATE_REF}/flet-build-template.zip"
)


def _succeeds(command: list[str]) -> bool:
    """명령을 조용히 실행해 종료 코드 0 여부만 돌려준다(존재 확인용)."""
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError:
        return False
    return result.returncode == 0


# -- Linux --------------------------------------------------------------------
def install_linux_deps() -> None:
    """flet(Flutter) Linux 데스크톱 빌드에 필요한 네이티브 패키지를 설치한다."""
    # 권한 상승 명령을 정한다(root면 불필요, sudo 없으면 설치 생략).
    sudo: list[str] = []
    if os.geteuid() != 0:
        if shutil.which("sudo"):
            sudo = ["sudo"]
        else:
            info("경고: root가 아니고 sudo도 없어 빌드 의존성 자동 설치를 건너뜁니다.")
            info("      clang, cmake, ninja, pkg-config, GTK3 개발 패키지를 수동 설치하세요.")
            return

    if shutil.which("apt-get"):
        info("Linux 빌드 의존성 설치 (apt)")
        check([*sudo, "apt-get", "update"])
        check(
            [*sudo, "apt-get", "install", "-y",
             "clang", "cmake", "ninja-build", "pkg-config", "libgtk-3-dev"]
        )
    elif shutil.which("dnf"):
        info("Linux 빌드 의존성 설치 (dnf)")
        # pkgconf-pkg-config는 최신 Fedora/RHEL 기준. 구형은 pkgconfig 가상 패키지일 수 있다.
        check(
            [*sudo, "dnf", "install", "-y",
             "clang", "cmake", "ninja-build", "pkgconf-pkg-config", "gtk3-devel"]
        )
    elif shutil.which("pacman"):
        info("Linux 빌드 의존성 설치 (pacman)")
        check([*sudo, "pacman", "-S", "--needed", "--noconfirm",
               "clang", "cmake", "ninja", "pkgconf", "gtk3"])
    else:
        info("경고: 지원되는 패키지 매니저(apt/dnf/pacman)를 찾지 못했습니다.")
        info("      clang, cmake, ninja, pkg-config, GTK3 개발 패키지를 수동 설치하세요.")


# -- macOS --------------------------------------------------------------------
def install_macos_deps() -> None:
    """macOS 앱 빌드에 필요한 Xcode CLT를 확인하고 CocoaPods를 설치한다."""
    # Xcode Command Line Tools가 없으면 macOS 앱 빌드 자체가 불가하므로 중단한다.
    if not _succeeds(["xcode-select", "-p"]):
        fail("Xcode Command Line Tools가 없습니다. 'xcode-select --install' 후 다시 시도하세요.")
    if shutil.which("pod") is None:
        if shutil.which("brew"):
            info("CocoaPods 설치 (brew)")
            check(["brew", "install", "cocoapods"])
        else:
            fail(
                "CocoaPods(pod)가 없고 Homebrew도 없습니다. "
                "'sudo gem install cocoapods' 등으로 설치 후 다시 시도하세요."
            )


# -- Windows ------------------------------------------------------------------
def _vswhere_path() -> Path:
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"


def _has_vc_tools(vswhere: Path) -> bool:
    """VC.Tools 워크로드가 설치돼 있는지 vswhere로 확인한다."""
    if not vswhere.exists():
        return False
    result = subprocess.run(
        [str(vswhere), "-products", "*", "-requires", _VC_TOOLS_COMPONENT,
         "-property", "installationPath"],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def _vs_install_path(vswhere: Path) -> str | None:
    """VC.Tools 보유 여부와 무관하게 설치된 VS 인스턴스 경로를 돌려준다(없으면 None)."""
    if not vswhere.exists():
        return None
    result = subprocess.run(
        [str(vswhere), "-products", "*", "-property", "installationPath"],
        capture_output=True, text=True,
    )
    lines = result.stdout.strip().splitlines()
    return lines[0] if lines else None


def ensure_vc_tools() -> None:
    """Visual Studio C++ 빌드 도구를 확인하고, 없으면 설치한다."""
    vswhere = _vswhere_path()
    if _has_vc_tools(vswhere):
        info("Visual Studio C++ 빌드 도구 확인됨")
        return

    info("Visual Studio C++ 빌드 도구가 없습니다. 설치를 진행합니다 (UAC 권한 요청이 뜰 수 있음).")
    vs_setup = vswhere.parent / "vs_installer.exe"
    common = ["--add", _VC_TOOLS_COMPONENT, "--includeRecommended",
              "--passive", "--norestart", "--wait"]

    if vs_setup.exists():
        # vs_installer.exe가 있으면 그것으로 직접 설치/수정한다. winget은 취소된 부분 설치를
        # '설치됨'으로 오인해 워크로드 추가를 건너뛰는 한계가 있어 우선하지 않는다.
        install_path = _vs_install_path(vswhere)
        if install_path:
            info("기존 Visual Studio에 C++ 워크로드를 추가합니다 (vs_installer modify).")
            verb = ["modify", "--installPath", install_path]
        else:
            info("Visual Studio Build Tools를 설치합니다 (vs_installer install).")
            verb = ["install", "--channelId", "VisualStudio.17.Release",
                    "--productId", "Microsoft.VisualStudio.Product.BuildTools"]
        info("    설치 창이 뜨며 수 분~수십 분 걸릴 수 있습니다. 끝까지 기다려 주세요.")
        subprocess.run([str(vs_setup), *verb, *common])
    elif shutil.which("winget"):
        info("winget으로 Visual Studio Build Tools를 설치합니다. 수 분~수십 분 걸릴 수 있습니다.")
        override = (
            f"--add {_VC_TOOLS_COMPONENT} --includeRecommended --passive --norestart --wait"
        )
        subprocess.run([
            "winget", "install", "--id", "Microsoft.VisualStudio.2022.BuildTools",
            "--accept-package-agreements", "--accept-source-agreements",
            "--override", override,
        ])
    else:
        fail(
            "Visual Studio Build Tools를 자동 설치할 수단(vs_installer/winget)이 없습니다.\n"
            "'Desktop development with C++' 워크로드를 수동 설치하세요:\n"
            "  https://visualstudio.microsoft.com/downloads/"
        )

    # vs_installer는 --wait를 줘도 백그라운드로 이어 설치하는 경우가 있어, 성공 판정은 재확인으로 한다.
    if not _has_vc_tools(vswhere):
        fail(
            "C++ 워크로드 설치를 확인하지 못했습니다.\n"
            "설치 관리자가 백그라운드에서 계속 진행 중일 수 있습니다.\n"
            "Visual Studio Installer 창에서 설치 완료를 확인한 뒤 이 스크립트를 다시 실행하세요."
        )
    info("Visual Studio C++ 빌드 도구 설치 완료")


# -- 공통 ---------------------------------------------------------------------
def verify_artifact(target: str) -> None:
    """flet이 에러를 내고도 0으로 끝나는 경우가 있어 결과물 존재를 직접 확인한다."""
    out_dir = REPO_ROOT / "build" / target
    if target == "windows":
        exes = sorted(out_dir.rglob("*.exe")) if out_dir.exists() else []
        if not exes:
            fail("빌드가 끝났지만 build/windows 에서 .exe를 찾지 못했습니다.")
        info(f"완료: {exes[0]}")
    else:
        if not out_dir.exists() or not any(out_dir.iterdir()):
            fail(f"빌드가 끝났지만 build/{target} 에 결과물이 없습니다.")
        info(f"완료: build/{target}/ 를 확인하세요.")


def _download_template_zip(dest: Path) -> None:
    """flet 빌드 템플릿 zip을 받는다.

    GitHub가 간헐적으로 zip 대신 HTML(500 'Unicorn!') 페이지를 반환하는 일이 있어,
    유효한 zip을 받을 때까지 재시도하고 끝내 실패하면 명확히 중단한다.
    """
    last_reason = ""
    for _attempt in range(5):
        try:
            urllib.request.urlretrieve(_FLET_TEMPLATE_URL, dest)
        except OSError as exc:  # HTTPError(5xx 등) 포함
            last_reason = str(exc)
        else:
            if zipfile.is_zipfile(dest):
                return
            last_reason = "다운로드 응답이 유효한 zip이 아님(GitHub 일시 오류 가능)"
        time.sleep(2)
    fail(f"flet 빌드 템플릿을 받지 못했습니다: {last_reason}\n  URL: {_FLET_TEMPLATE_URL}")


def _patch_storage_location(main_dart: Path) -> None:
    """빌드 템플릿 main.dart의 데스크톱 앱 데이터/임시 폴더를 실행 파일 옆 storage/로 바꾼다.

    원본은 <Documents>/flet/<app>에 폴더를 만들지만, 이를 실행 파일과 같은 폴더의
    storage/data, storage/temp로 바꿔 어디서 실행하든 자기 폴더 안에 데이터를 둔다(포터블).
    'data'가 아니라 'storage'를 쓰는 이유는 Flutter가 <exe>/data를 번들 용도로
    예약(app.so 등)하기 때문이다.
    """
    text = main_dart.read_text(encoding="utf-8")
    original = (
        "    if (defaultTargetPlatform != TargetPlatform.iOS &&\n"
        "        defaultTargetPlatform != TargetPlatform.android) {\n"
        "      // append app name to the path and create dir\n"
        "      PackageInfo packageInfo = await PackageInfo.fromPlatform();\n"
        '      appDataPath = path.join(appDataPath, "flet", packageInfo.packageName);\n'
        "      if (!await Directory(appDataPath).exists()) {\n"
        "        await Directory(appDataPath).create(recursive: true);\n"
        "      }\n"
        "    }\n"
    )
    replacement = (
        "    if (defaultTargetPlatform != TargetPlatform.iOS &&\n"
        "        defaultTargetPlatform != TargetPlatform.android) {\n"
        "      // Portable desktop build: store app data next to the executable so the\n"
        "      // app runs from any folder and never depends on Documents/OneDrive.\n"
        "      // NOTE: use a 'storage/' subfolder, not 'data/', because Flutter reserves\n"
        "      // <exe>/data for its own bundle (app.so, icudtl.dat, flutter_assets).\n"
        "      final exeDir = path.dirname(Platform.resolvedExecutable);\n"
        '      appDataPath = path.join(exeDir, "storage", "data");\n'
        '      appTempPath = path.join(exeDir, "storage", "temp");\n'
        "      for (final d in [appDataPath, appTempPath]) {\n"
        "        if (!await Directory(d).exists()) {\n"
        "          await Directory(d).create(recursive: true);\n"
        "        }\n"
        "      }\n"
        "    }\n"
    )
    if original not in text:
        fail(
            "flet 템플릿 main.dart 구조가 예상과 달라 저장 위치 패치를 적용하지 못했습니다.\n"
            f"  flet/템플릿 버전이 바뀌었을 수 있습니다(_FLET_TEMPLATE_REF={_FLET_TEMPLATE_REF})."
        )
    main_dart.write_text(text.replace(original, replacement), encoding="utf-8")


def prepare_portable_template() -> Path:
    """flet 빌드 템플릿을 확보·패치하고 cookiecutter 템플릿 루트 경로를 돌려준다.

    유효한 cookiecutter 캐시가 있으면 재사용하고, 없으면 버전에 맞춰 내려받는다.
    """
    work = REPO_ROOT / ".flet-template"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    zip_path = work / "flet-build-template.zip"
    cached = Path.home() / ".cookiecutters" / "flet-build-template.zip"
    if cached.is_file() and zipfile.is_zipfile(cached):
        info("flet 빌드 템플릿 캐시 재사용")
        shutil.copyfile(cached, zip_path)
    else:
        info("flet 빌드 템플릿 다운로드")
        _download_template_zip(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(work)

    root = work / "build"  # cookiecutter.json 이 있는 템플릿 루트
    main_dart = root / "{{cookiecutter.out_dir}}" / "lib" / "main.dart"
    if not main_dart.is_file():
        fail(f"빌드 템플릿에서 main.dart를 찾지 못했습니다: {main_dart}")
    _patch_storage_location(main_dart)
    info("앱 데이터 저장 위치를 실행 파일 옆 storage/ 로 패치함")
    return root


def main() -> int:
    require_uv()

    system = platform.system()
    if system == "Windows":
        target = "windows"
        ensure_vc_tools()
    elif system == "Darwin":
        target = "macos"
        install_macos_deps()
    elif system == "Linux":
        target = "linux"
        install_linux_deps()
    else:
        fail(f"지원하지 않는 OS입니다: {system}")

    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])

    # flet build의 진행 표시(rich)는 체크마크 등 이모지를 stdout에 쓰는데, 한국어
    # Windows 콘솔 기본 코덱(cp949)으로는 인코딩할 수 없어 UnicodeEncodeError로
    # 빌드가 죽는다. 자식 Python을 UTF-8 모드로 강제해 stdout 인코딩을 utf-8로
    # 바꿔 회피한다(다른 OS에선 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    template_root = prepare_portable_template()

    info(f"flet build {target}")
    check(
        ["uv", "run", "flet", "build", target, "--product", _PRODUCT, "--org", _ORG,
         "--template", str(template_root)],
        env=build_env,
    )

    verify_artifact(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
