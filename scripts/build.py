#!/usr/bin/env python3
"""flet 네이티브 앱 빌드 스크립트. 실행한 OS를 감지해 빌드한다.

자동 처리:
  - Windows: Visual Studio "Desktop development with C++" 워크로드가 없으면 설치한다.
  - macOS:   Xcode Command Line Tools를 확인하고 CocoaPods를 Homebrew로 설치한다.
  - Linux:   clang/cmake/ninja/pkg-config/GTK3 개발 패키지를 패키지 매니저로 설치한다.
             (root가 아니면 sudo를 쓰며, sudo가 없으면 설치를 건너뛰고 안내한다.)
  - Flutter SDK는 flet build가 필요 시 자동으로 내려받는다.

사용:
    python scripts/build.py            # flet build(Flutter 네이티브). 폴더째 배포.
    python scripts/build.py --onefile  # flet pack(PyInstaller). 단일 exe 1개.

결과물:
  - 기본:      build/<platform>/ 아래 앱 번들(실행파일 + DLL + data/). 폴더째 배포한다.
  - --onefile: dist/ 아래 단일 실행파일(Windows .exe / Linux 바이너리 / macOS .app).
(개발 중 빠른 실행은 'python scripts/run.py --gui')

앱 데이터 저장 위치: 기본(flet build) 데스크톱 빌드는 실행 파일과 같은 폴더의
storage/ 에 저장한다(flet 기본값인 <Documents>/flet/<app> 대신). 앱을 폴더째
배포하면 어디서 실행하든 자기 폴더 안에 데이터를 두는 포터블 동작이며, OneDrive로
옮겨진 Documents 등에 의존하지 않는다. 단, 쓰기 가능한 위치에서 실행해야 한다
(예: Program Files 아래 X).

--onefile(단일 실행파일): Flutter/네이티브 툴체인 없이 PyInstaller로 exe 하나만
만든다. exe만 떼서 배포·실행할 수 있는 게 장점이지만, 백신 오탐·느린 첫 실행
(temp에 압축 해제)·큰 용량 등 단점이 있고, 위의 포터블 storage/ 패치는 적용되지
않아 flet 기본 데이터 경로(<Documents>/flet/<app>)를 쓴다. 배포보다 임시 실행용
폴백으로 권장한다.
"""

from __future__ import annotations

import argparse
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

# `flet pack`(PyInstaller) 단일 실행파일 빌드 설정.
# 진입점은 flet build와 같은 셔임(src/main.py)이다. pack은 이 스크립트의 디렉터리
# (src/)를 import 경로에 올리므로 naver_post_crawler 패키지를 그대로 찾을 수 있다.
_PACK_ENTRY = REPO_ROOT / "src" / "main.py"
_PACK_NAME = "naver-post-crawler"  # 생성될 실행파일 이름(flet build 결과물과 동일)
_PACK_DIST = REPO_ROOT / "dist"  # 단일 실행파일이 놓일 디렉터리
# flet pack은 cwd/build 디렉터리를 통째로 지운다. flet build 결과물(build/windows)을
# 보호하려고 pack은 전용 작업 디렉터리에서 돌린다(그 안의 build/만 정리됨).
_PACK_WORK = REPO_ROOT / ".pack-build"

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


def _pack_artifact_path() -> Path:
    """현재 OS에서 flet pack이 만드는 단일 실행파일/번들의 경로를 돌려준다."""
    system = platform.system()
    if system == "Windows":
        return _PACK_DIST / f"{_PACK_NAME}.exe"
    if system == "Darwin":
        return _PACK_DIST / f"{_PACK_NAME}.app"  # macOS는 .app 번들
    return _PACK_DIST / _PACK_NAME


def _clean_prior_artifact() -> None:
    """이전 단일 실행파일을 미리 지운다. 잠겨 있으면 원인을 짚어 명확히 안내한다.

    flet pack은 dist를 rmtree(ignore_errors=True)로 지우는데, 결과물이 잠겨 있으면
    (앱이 실행 중이거나 백신이 검사 중) 조용히 남겨두고 PyInstaller의 EXE 단계가
    os.remove에서 PermissionError로 죽는다 — 불친절한 트레이스백이 그대로 노출된다.
    여기서 먼저 지워 보고, 실패하면 사용자가 조치할 수 있게 원인을 안내한다.
    """
    artifact = _pack_artifact_path()
    if not artifact.exists():
        return
    try:
        if artifact.is_dir():  # macOS .app 번들
            shutil.rmtree(artifact)
        else:
            artifact.unlink()
    except OSError:
        fail(
            f"기존 결과물을 지울 수 없습니다(잠김): {artifact}\n"
            "  실행 중인 앱을 모두 닫은 뒤 다시 시도하세요.\n"
            "  (백신 실시간 검사가 파일을 잡고 있을 수도 있습니다.)"
        )


def verify_pack_artifact() -> None:
    """flet pack(PyInstaller) 결과물(단일 실행파일/번들)이 생겼는지 확인한다."""
    if not _PACK_DIST.exists():
        fail(f"빌드가 끝났지만 {_PACK_DIST} 가 없습니다.")
    artifact = _pack_artifact_path()
    if not artifact.exists():
        fail(f"빌드가 끝났지만 결과물을 찾지 못했습니다: {artifact}")
    info(f"완료: {artifact}")


def pack_app(build_env: dict[str, str]) -> None:
    """`flet pack`으로 단일 실행파일을 만든다(Flutter 툴체인 불필요한 폴백 경로).

    flet build와 달리 DLL·data 폴더가 따로 필요 없는 단일 파일이 나온다. 대신
    백신 오탐·느린 첫 실행·큰 용량 등의 단점이 있고, 포터블 storage/ 패치(=실행
    파일 옆 저장)는 적용되지 않아 flet 기본 데이터 경로를 쓴다.
    """
    # 이전 결과물이 잠겨 있으면(앱 실행 중 등) 먼저 명확히 안내하고 멈춘다.
    # 그러지 않으면 flet pack이 마지막 단계에서 PermissionError 트레이스백으로 죽는다.
    _clean_prior_artifact()

    # flet pack은 cwd/build를 통째로 지우므로 flet build 결과물(build/windows)을
    # 건드리지 않도록 전용 작업 디렉터리를 만들어 그 안에서 실행한다.
    if _PACK_WORK.exists():
        shutil.rmtree(_PACK_WORK)
    _PACK_WORK.mkdir(parents=True)

    info("flet pack (단일 실행파일)")
    check(
        ["uv", "run", "flet", "pack", str(_PACK_ENTRY),
         "--name", _PACK_NAME, "--product-name", _PRODUCT,
         "--distpath", str(_PACK_DIST), "--yes"],
        env=build_env,
        cwd=_PACK_WORK,
    )

    # PyInstaller 중간 산출물(작업 디렉터리)은 결과물이 아니므로 정리한다.
    shutil.rmtree(_PACK_WORK, ignore_errors=True)
    verify_pack_artifact()


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="flet build(Flutter) 대신 flet pack(PyInstaller)으로 단일 실행파일을 만든다. "
        "DLL·data 폴더 없이 exe 하나만 배포할 수 있으나 백신 오탐·느린 첫 실행 등 단점이 있다.",
    )
    args = parser.parse_args()

    require_uv()

    # flet build의 진행 표시(rich)는 체크마크 등 이모지를 stdout에 쓰는데, 한국어
    # Windows 콘솔 기본 코덱(cp949)으로는 인코딩할 수 없어 UnicodeEncodeError로
    # 빌드가 죽는다. 자식 Python을 UTF-8 모드로 강제해 stdout 인코딩을 utf-8로
    # 바꿔 회피한다(다른 OS에선 무해). flet pack의 PyInstaller 로그에도 같이 적용된다.
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    if args.onefile:
        # PyInstaller 폴백 경로: Flutter/네이티브 툴체인이 필요 없으므로 OS별
        # 빌드 의존성 설치를 건너뛴다. 필요한 건 pyinstaller(dev 그룹)와 flet-desktop뿐.
        info("의존성 동기화 (uv sync)")
        check(["uv", "sync"])
        pack_app(build_env)
        return 0

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
