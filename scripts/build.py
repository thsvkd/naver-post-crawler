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

참고: Flutter/네이티브 툴체인 설치가 부담되면 flet build 대신 PyInstaller 기반
      'uv run flet pack src/naver_blog_crawler/gui.py' 로 단일 실행파일을 만들 수 있다.
      (백신 오탐·큰 용량 등 단점이 있어 배포보다 임시 실행용 폴백으로 권장.)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from _common import REPO_ROOT, check, fail, info, require_uv

# flet build 메타데이터(두 플랫폼 빌드가 동일하게 쓴다).
_PRODUCT = "Naver Blog Backup"
_ORG = "com.thsvkd"

# Visual Studio C++ 빌드 도구 워크로드(컴포넌트) 식별자.
_VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"


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

    info(f"flet build {target}")
    check(
        ["uv", "run", "flet", "build", target, "--product", _PRODUCT, "--org", _ORG],
        env=build_env,
    )

    verify_artifact(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
