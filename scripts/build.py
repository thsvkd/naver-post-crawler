#!/usr/bin/env python3
"""``flet build`` 네이티브 앱 빌드 + Velopack 설치기 패키징.

실행한 OS를 감지해 그 OS용 데스크톱 앱을 만들고, 그것을 Velopack 설치기/업데이트
패키지로 포장한다. Windows 빌드는 Windows에서만, macOS 빌드는 macOS에서만 된다
(``flet build``와 ``vpk`` 양쪽의 제약이라 우회할 수 없다).

사용:
    uv run python scripts/build.py

결과물:
    dist/naver-post-crawler-<target>/   flet build 번들(설치기의 원본)
    dist/velopack/                      Windows: *-Setup.exe, *.nupkg, releases.win.json
                                        macOS:   *-Setup.pkg, *.nupkg, releases.osx.json
                                        → GitHub 릴리스에 올리면 자동 업데이트가 동작한다.

서명: 기본은 **미서명**이다. ``NPC_SIGN_*`` 환경변수를 채우면 scripts/sign.py가 인자를
만들어 붙인다(자세한 내용은 그 모듈 참고).

사전 준비:
    - Windows: Visual Studio "Desktop development with C++" 워크로드(없으면 안내).
    - macOS: Xcode 명령행 도구.
    - 공통: Velopack CLI(``dotnet tool install -g vpk``). Flutter SDK는 flet build가 받아 온다.

(개발 중 빠른 실행은 'python scripts/run.py --gui')
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
from pathlib import Path

import flet_template
import sign
from _common import REPO_ROOT, check, fail, info, require_uv, run, sync_version

from naver_post_crawler.velopack_update import REPO_URL

# flet build 메타데이터.
_PRODUCT = "Naver Blog Backup"
_ORG = "com.thsvkd"
_AUTHORS = "thsvkd"

# Velopack 패키징 식별자. **바꾸면 기존 설치본과의 연결이 끊긴다**(설치 경로·업데이트
# 식별자가 이 값으로 정해진다). 앱 데이터 폴더 이름(naver-post-crawler)과 일부러 다르게
# 둔다 — Windows 기본 설치 경로가 ``%LocalAppData%\<PackId>\`` 라서, 같은 이름이면
# 언인스톨할 때 사용자 쿠키까지 함께 지워진다.
PACK_ID = "NaverPostCrawler"
# Windows 번들 루트의 앱 실행 파일 이름(vpk --mainExe).
APP_EXE_WINDOWS = "naver-post-crawler.exe"

# 타깃별 Velopack 채널. 이 값이 릴리스 피드 파일 이름(releases.<채널>.json)을 정한다.
# Windows와 macOS 산출물을 같은 GitHub 릴리스 태그에 함께 올려도 파일명이 겹치지 않는 이유다.
_CHANNELS = {"windows": "win", "macos": "osx"}

# Visual Studio C++ 빌드 도구 워크로드 식별자.
_VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"


def target_for(system: str) -> str:
    """``platform.system()`` 값을 flet build 타깃 이름으로 바꾼다.

    Linux는 배포 대상이 아니다(D-13). 조용히 windows로 떨어지면 엉뚱한 산출물을 만들므로
    지원하지 않는 OS에서는 즉시 중단한다.
    """
    target = {"Windows": "windows", "Darwin": "macos"}.get(system)
    if target is None:
        fail(f"지원하지 않는 OS입니다: {system} (배포 대상은 Windows와 macOS입니다)")
    return target


def channel_for(target: str) -> str:
    """타깃의 Velopack 채널 이름."""
    channel = _CHANNELS.get(target)
    if channel is None:
        fail(f"채널을 알 수 없는 타깃입니다: {target}")
    return channel


def current_target() -> str:
    """현재 OS의 빌드 타깃."""
    return target_for(platform.system())


# -- Windows 사전 점검 --------------------------------------------------------
def _vswhere_path() -> Path:
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    return Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"


def ensure_windows_toolchain() -> None:
    """Windows 네이티브 빌드에 필요한 VS C++ 빌드 도구를 확인한다(없으면 안내 후 중단).

    ``flet pack``(PyInstaller)에는 필요 없던 요구사항이다 — ``flet build``는 Flutter 러너를
    실제로 컴파일하므로 네이티브 툴체인이 있어야 한다.
    """
    vswhere = _vswhere_path()
    if vswhere.exists():
        result = subprocess.run(
            [
                str(vswhere),
                "-products",
                "*",
                "-requires",
                _VC_TOOLS_COMPONENT,
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            info("Visual Studio C++ 빌드 도구 확인됨")
            return
    fail(
        "Visual Studio C++ 빌드 도구('Desktop development with C++')가 필요합니다.\n"
        "  https://visualstudio.microsoft.com/downloads/ 에서 Build Tools를 설치하거나\n"
        "  winget install --id Microsoft.VisualStudio.2022.BuildTools \\\n"
        '    --override "--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 '
        '--includeRecommended --passive"'
    )


def flet_version() -> str:
    """빌드에 쓰이는 flet 버전(패치용 빌드 템플릿을 같은 버전으로 받으려고 확인한다).

    ``flet build``가 템플릿 태그로 쓰는 값과 정확히 같아야 하므로, pyproject의 핀을
    파싱하지 않고 동기화된 환경에서 직접 읽는다.
    """
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "-c",
            "import flet.version as v; print(v.flet_version)",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    version = result.stdout.strip()
    if result.returncode != 0 or not version:
        fail(f"flet 버전을 확인하지 못했습니다: {result.stderr.strip() or result.stdout.strip()}")
    return version


# -- 커맨드 조립(순수 함수) ---------------------------------------------------
def flet_build_command(target: str, *, template_dir: Path | None) -> list[str]:
    """``flet build`` 실행 커맨드.

    Windows에서만 패치된 템플릿을 넘긴다 — 러너 진입점에서 Velopack 훅을 처리하고 첫 창을
    앱 크기로 만들기 위해서다(scripts/flet_template.py). macOS 러너는 패치하지 않으므로
    ``--template``을 붙이면 존재하지 않는 패치를 요구하는 셈이 된다.
    """
    cmd = ["uv", "run", "--no-sync", "flet", "build", target, "--product", _PRODUCT, "--org", _ORG]
    if template_dir is not None:
        cmd += ["--template", str(template_dir)]
    return cmd


def macos_main_exe(app_bundle: Path) -> str:
    """``.app`` 번들이 실행하는 바이너리 이름.

    번들이 스스로 밝히는 이름(``Info.plist``의 ``CFBundleExecutable``)을 우선 쓰고, 읽지
    못하면 ``Contents/MacOS``에 있는 실행 파일에서 찾는다. 어느 쪽으로도 알 수 없으면
    중단한다 — 조용히 packId로 떨어지면 vpk가 존재하지 않는 경로를 찾다 실패한다.
    """
    plist_path = app_bundle / "Contents" / "Info.plist"
    if plist_path.is_file():
        try:
            with plist_path.open("rb") as handle:
                name = plistlib.load(handle).get("CFBundleExecutable")
        except (OSError, plistlib.InvalidFileException):
            name = None
        if name:
            return str(name)

    macos_dir = app_bundle / "Contents" / "MacOS"
    binaries = sorted(p for p in macos_dir.glob("*") if p.is_file()) if macos_dir.is_dir() else []
    if len(binaries) == 1:
        return binaries[0].name
    fail(
        f"{app_bundle}에서 실행 파일 이름을 정하지 못했습니다"
        f"(Info.plist의 CFBundleExecutable 없음, Contents/MacOS 항목 {len(binaries)}개)."
    )


def prune_bundle(app_bundle: Path) -> list[Path]:
    """배포 번들에서 **바깥을 가리키는 심볼릭 링크**를 지우고 그 목록을 돌려준다.

    ``flet build macos``는 site-packages에 ``.pod -> ~/.pub-cache/.../darwin`` 같은 링크를
    남긴다. 빌드 머신의 절대 경로라 사용자 머신에서는 어차피 깨진 링크이고, 그 대상 안에
    같은 링크가 또 있어 **트리 순회가 무한 재귀한다**(실측: vpk pack이 "path is too long"
    으로 죽었다). 배포 번들은 자기 완결적이어야 하므로 여기서 걷어낸다.

    번들 안을 가리키는 링크는 그대로 둔다 — macOS 프레임워크 구조(``Versions/Current`` 등)가
    내부 심볼릭 링크로 이루어져 있어 지우면 앱이 깨진다.
    """
    root = app_bundle.resolve()
    removed: list[Path] = []
    for path in app_bundle.rglob("*"):
        if not path.is_symlink():
            continue
        # 링크가 가리키는 곳을 번들 기준으로 판정한다. 대상이 없어도(깨진 링크) 경로만 본다.
        target = Path(os.path.realpath(path))
        if target == root or root in target.parents:
            continue
        path.unlink()
        removed.append(path)
    return removed


def velopack_output_dir() -> Path:
    """Velopack 산출물 폴더(릴리스에 올릴 파일들이 모이는 곳)."""
    return REPO_ROOT / "dist" / "velopack"


def vpk_pack_args(target: str, *, bundle_dir: Path, version: str) -> list[str]:
    """``vpk pack`` 인자(vpk 실행 파일 경로는 뺀 나머지).

    타깃별로 인자 체계가 다르다. Windows는 번들 폴더와 그 안의 실행 파일 이름을 주고,
    macOS는 ``.app`` 번들 자체를 준다(진입점은 Info.plist에 있다). 서명 인자는 환경변수가
    채워졌을 때만 붙는다(기본은 미서명).
    """
    args = [
        "pack",
        "--packId",
        PACK_ID,
        "--packVersion",
        version,
        "--packDir",
        str(bundle_dir),
        "--packTitle",
        _PRODUCT,
        "--packAuthors",
        _AUTHORS,
        "--channel",
        channel_for(target),
        "--outputDir",
        str(velopack_output_dir()),
    ]
    if target == "windows":
        args += ["--mainExe", APP_EXE_WINDOWS]
        params = sign.velopack_sign_params_win()
        if params:
            args += ["--signParams", params]
    else:
        # macOS도 --mainExe가 필요하다. 생략하면 vpk가 packId를 실행 파일 이름으로 가정하고
        # <bundle>/Contents/MacOS/<packId>를 찾다 실패한다(실측: packId는 NaverPostCrawler인데
        # 실제 바이너리는 naver-post-crawler라 "Could not find main application executable").
        args += ["--mainExe", macos_main_exe(bundle_dir)]
        # 설치기(.pkg)만 배포한다(D-2). Portable.zip은 올리지 않으므로 만들지도 않는다.
        args.append("--noPortable")
        args += sign.velopack_sign_args_macos()
    return args


def vpk_download_args(target: str) -> list[str]:
    """``vpk download github`` 인자 — 델타 계산의 기준이 될 이전 릴리스를 받아 온다.

    채널이 pack과 같아야 한다. 다르면 기준을 못 찾아 매번 전체 패키지를 만든다.
    """
    return [
        "download",
        "github",
        "--repoUrl",
        REPO_URL,
        "--outputDir",
        str(velopack_output_dir()),
        "--channel",
        channel_for(target),
    ]


# -- 결과물 정리/검증 --------------------------------------------------------
def stash_output(target: str) -> Path:
    """flet build 결과(build/<target>)를 배포 폴더로 옮긴다."""
    src = REPO_ROOT / "build" / target
    if not src.exists() or not any(src.iterdir()):
        fail(f"빌드가 끝났지만 build/{target}에 결과물이 없습니다.")
    dst = REPO_ROOT / "dist" / f"naver-post-crawler-{target}"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


def verify_artifact(dst: Path, target: str) -> None:
    """배포 폴더에 그 타깃의 실행 산출물이 실제로 생겼는지 확인한다.

    flet이 오류를 내고도 종료 코드 0으로 끝나는 경우가 있어, "폴더가 비어 있지 않다"로는
    부족하다. Windows는 번들 루트의 ``.exe``를, macOS는 ``.app`` 번들을 확인한다.
    """
    if target == "windows":
        exes = sorted(dst.glob("*.exe"))
        if not exes:
            fail(f"빌드가 끝났지만 {dst} 최상위에서 앱 .exe를 찾지 못했습니다.")
        info(f"완료(앱 실행파일): {exes[0]}")
        return
    apps = sorted(dst.glob("*.app"))
    if not apps:
        fail(f"빌드가 끝났지만 {dst}에서 .app 번들을 찾지 못했습니다.")
    info(f"완료(앱 번들): {apps[0]}")


def app_bundle(dst: Path) -> Path:
    """macOS 배포 폴더 안의 ``.app`` 번들 경로(vpk pack의 packDir)."""
    apps = sorted(dst.glob("*.app"))
    if not apps:
        fail(f"{dst}에서 .app 번들을 찾지 못했습니다.")
    return apps[0]


# -- Velopack 패키징 ---------------------------------------------------------
def find_vpk() -> str:
    """Velopack CLI(vpk) 경로. PATH 또는 dotnet 글로벌 툴 기본 위치에서 찾는다."""
    exe = shutil.which("vpk")
    if exe:
        return exe
    candidate = Path.home() / ".dotnet" / "tools" / ("vpk.exe" if os.name == "nt" else "vpk")
    if candidate.exists():
        return str(candidate)
    fail("vpk(Velopack CLI)를 찾지 못했습니다. 설치: dotnet tool install -g vpk")


def velopack_pack(
    *,
    bundle_dir: Path,
    version: str,
    target: str,
    vpk: str,
    runner=run,
) -> Path:
    """번들을 Velopack 설치기 + 업데이트 패키지로 만든다.

    기존 GitHub 릴리스를 **먼저 받아**(``vpk download github``) 그 위에 델타를 만든다.
    첫 릴리스거나 네트워크가 안 되면 델타 없이 전체 릴리스로 진행한다.
    """
    out = velopack_output_dir()
    out.mkdir(parents=True, exist_ok=True)

    info("기존 Velopack 릴리스 조회(델타 기준)…")
    if runner([vpk, *vpk_download_args(target)], cwd=REPO_ROOT) != 0:
        info("  기존 릴리스 없음/조회 실패 → 전체 릴리스로 진행(델타 없음).")

    info("Velopack 패키징…")
    pack_cmd = [vpk, *vpk_pack_args(target, bundle_dir=bundle_dir, version=version)]
    if runner(pack_cmd, cwd=REPO_ROOT) != 0:
        fail("vpk pack이 실패했습니다.")
    return out


def main() -> int:
    require_uv()
    target = current_target()

    if target == "windows":
        ensure_windows_toolchain()

    # pyproject.toml(SSOT)의 버전을 __init__.py에 반영한 뒤(flet build가 이 파일을 그대로
    # 복사해 번들에 담으므로 빌드 전에 최신이어야 한다) 빌드에 쓸 버전으로 쓴다.
    version = sync_version()

    # flet build의 진행 표시(rich)가 이모지를 stdout에 쓰는데 한국어 Windows 콘솔 기본
    # 코덱(cp949)으로는 인코딩할 수 없어 UnicodeEncodeError로 죽는다. 자식 Python을
    # UTF-8 모드로 강제해 회피한다(다른 OS엔 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])

    template_dir = flet_template.prepare(flet_version()) if target == "windows" else None
    info(f"flet build {target}")
    check(flet_build_command(target, template_dir=template_dir), env=build_env)

    dst = stash_output(target)
    verify_artifact(dst, target)
    if target == "windows":
        # 앱 exe 서명(NPC_SIGN_* 설정 시). 미지정이면 미서명으로 계속한다.
        sign.maybe_sign_bundle(dst)

    if target == "windows":
        pack_dir = dst
    else:
        pack_dir = app_bundle(dst)
        # 빌드 머신 경로를 가리키는 링크를 걷어낸다(없으면 무동작). 남겨 두면 vpk가 트리를
        # 순회하다 무한 재귀에 빠지고, 사용자 머신에서는 어차피 깨진 링크다.
        pruned = prune_bundle(pack_dir)
        if pruned:
            info(f"번들 밖을 가리키는 심볼릭 링크 {len(pruned)}개 제거: {pruned[0].name} …")
    out = velopack_pack(bundle_dir=pack_dir, version=version, target=target, vpk=find_vpk())
    info(f"Velopack 산출물: {out}")
    info(f"릴리스 업로드는 'python scripts/deploy.py'로 진행하세요 (태그 v{version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
