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
    - macOS: **전체 Xcode** + CocoaPods(Command Line Tools만으로는 안 된다 — 없으면 안내).
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

# 사람이 작성하는 릴리스 노트 파일 이름. Velopack 산출물이 아니므로 빌드가 산출 폴더를
# 비울 때 **이 이름만 남긴다**. deploy.py가 읽는 이름과 반드시 같아야 해서 여기서만
# 정의하고 deploy.py는 이 값을 가져다 쓴다(따로 적으면 어긋난 순간 노트가 지워진다).
RELEASE_NOTES = "RELEASE_NOTES.md"

# serious_python_windows 플러그인이 번들에 넣는 CRT DLL. 그 플러그인의 CMakeLists가
# ``$ENV{WINDIR}/System32``에서 가져오는데, Visual Studio가 주는 cmake.exe가 32비트라
# 그 경로가 WOW64로 SysWOW64에 리다이렉트된다. vcruntime140_1.dll은 **x64 전용**이라
# SysWOW64에는 존재할 수 없어 빌드가 죽는다(실측). 공식 MSVC redist 폴더에서 x64 DLL을
# 모아 두고 WINDIR을 그쪽으로 돌려 해결한다 — 시스템 디렉터리는 건드리지 않는다.
WINDOWS_CRT_DLLS = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
_MSVC_REDIST_BASE = r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Redist\MSVC"

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


# -- 산출물 이름 규약(build와 deploy의 단일 소스) ------------------------------
# Velopack이 nupkg 이름에서 채널 접미사를 빼는 유일한 조합은 **Windows 타깃 + win 채널**
# 이다. vpk 1.2.0의 DefaultName.GetSuggestedReleaseName을 역컴파일해 확인한 규칙이며,
# 그 밖에는 호스트 OS와 무관하게 항상 ``-<채널>``이 붙는다("채널이 그 OS의 기본 채널이면
# 뺀다"가 아니다 — macOS/osx 조합에는 그 면제가 적용되지 않는다).
#
# 실측(macOS 호스트, vpk 1.2.0): --channel osx → *-<ver>-osx-full.nupkg (접미사 유지).
# 실측(Windows 호스트): 실제 배포된 에셋이 *-<ver>-full.nupkg (접미사 없음).
_NO_CHANNEL_SUFFIX_TARGET = "windows"


def setup_glob(target: str) -> str:
    """설치기 파일 글롭. Windows는 .exe, macOS는 .pkg다."""
    return "*-Setup.exe" if target == "windows" else "*-Setup.pkg"


def releases_json_name(target: str) -> str:
    """업데이트 피드 파일 이름. 앱의 GithubSource가 **이름 완전 일치**로만 찾는다."""
    return f"releases.{channel_for(target)}.json"


def full_nupkg_glob(target: str, version: str) -> str:
    """이번 버전 전체 업데이트 패키지 글롭(위 접미사 규칙 참고)."""
    suffix = "" if target == _NO_CHANNEL_SUFFIX_TARGET else f"-{channel_for(target)}"
    return f"*-{version}{suffix}-full.nupkg"


def verify_velopack_output(out: Path, target: str, version: str) -> None:
    """vpk가 **기대한 이름 그대로** 산출물을 냈는지 확인한다.

    이름이 조금이라도 다르면 빌드는 성공한 것처럼 보이는데 deploy.py가 파일을 못 찾거나
    (업로드 누락) 앱이 피드를 못 읽어 "자동 업데이트만 조용히 안 되는" 상태가 된다.
    그래서 여기서 끊고, 실패 시 실제 파일 목록을 그대로 출력해 눈으로 고칠 수 있게 한다.

    델타(*-delta.nupkg)는 첫 릴리스에 없으므로 검사하지 않는다.
    """

    def listing() -> str:
        names = sorted(p.name for p in out.glob("*"))
        return "\n  ".join(names) if names else "(비어 있음)"

    channel = channel_for(target)
    installer = setup_glob(target)
    if not list(out.glob(installer)):
        fail(f"Velopack 설치기({installer})를 찾지 못했습니다.\n{out} 실제 내용:\n  {listing()}")
    feed = releases_json_name(target)
    if not (out / feed).is_file():
        fail(f"업데이트 피드 {feed}가 없습니다(채널={channel}).\n{out} 실제 내용:\n  {listing()}")
    full = full_nupkg_glob(target, version)
    if not list(out.glob(full)):
        fail(f"전체 업데이트 패키지({full})를 찾지 못했습니다.\n{out} 실제 내용:\n  {listing()}")
    info(f"Velopack 산출물 검증 통과(채널={channel}, 버전={version}).")


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


# -- macOS 사전 점검 ----------------------------------------------------------
# vpk의 OsxBuildTools가 .pkg를 만들며 직접 호출하는 명령들. 전부 CLT에 들어 있다.
_MACOS_TOOLS = ("pkgbuild", "productbuild", "ditto", "codesign", "plutil")
_XCODE_CLT_HINT = "Xcode Command Line Tools가 필요합니다: xcode-select --install"
# Flutter의 macOS 데스크톱 빌드는 **전체 Xcode**를 요구한다(CLT로는 안 된다).
# 함정: CLT만 깔린 머신에서도 `xcode-select -p`는 성공하고(/Library/Developer/CommandLineTools
# 를 출력) pkgbuild·ditto·codesign도 전부 CLT에 들어 있어 존재 확인을 통과한다. 그래서
# 순진한 점검은 초록불을 준 뒤 Flutter SDK를 다 내려받고 몇 분 지나서야 `flet build macos`가
# "Xcode installation is incomplete"로 죽는다(yt-knowledge-extractor에서 실제로 겪었다).
# 그 구분이 되는 명령이 xcodebuild다 — CLT 전용 환경에서는 실행 자체가 실패한다. 그래서
# 경로 문자열이 아니라 이 명령으로 판정한다(Xcode를 /Applications 밖에 두거나 여러 버전을
# xcode-select로 전환하는 경우까지 맞다).
_XCODE_FULL_HINT = (
    "전체 Xcode가 필요합니다(Command Line Tools만으로는 macOS 앱을 빌드할 수 없습니다).\n"
    "  1) App Store에서 Xcode를 설치한 뒤\n"
    "  2) sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer\n"
    "  3) sudo xcodebuild -runFirstLaunch"
)
# flet은 flet_desktop 등 Flutter 플러그인을 쓰고, macOS 플러그인은 CocoaPods로 엮인다.
# 없으면 Flutter 컴파일 단계까지 간 뒤에 죽으므로 미리 잡는다.
_COCOAPODS_HINT = (
    "CocoaPods가 필요합니다(Flutter 플러그인을 macOS에서 엮는 데 씁니다).\n"
    "  brew install cocoapods   (또는 sudo gem install cocoapods)"
)


def ensure_macos_toolchain() -> None:
    """macOS 네이티브 빌드/패키징에 필요한 도구를 확인한다(없으면 안내 후 중단).

    빌드를 시작하기 **전에** 다 확인한다 — 여기서 놓치면 Flutter SDK 다운로드와 컴파일에
    수 분을 쓴 뒤에야 실패해서, 원인이 환경 문제였다는 게 한참 뒤에 드러난다.
    """
    try:
        result = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True)
    except OSError:  # xcode-select 자체가 없는(=CLT 미설치) 경우
        fail(_XCODE_CLT_HINT)
    if result.returncode != 0:
        fail(_XCODE_CLT_HINT)
    developer_dir = result.stdout.strip()

    missing = [tool for tool in _MACOS_TOOLS if shutil.which(tool) is None]
    if missing:
        fail(f"{_XCODE_CLT_HINT}\n  (없는 명령: {', '.join(missing)} — vpk가 직접 호출한다)")

    # 전체 Xcode 판정. CLT 전용이면 여기서 걸린다.
    try:
        xcodebuild = subprocess.run(["xcodebuild", "-version"], capture_output=True, text=True)
    except OSError:
        fail(f"{_XCODE_FULL_HINT}\n  (현재 활성 개발자 디렉터리: {developer_dir})")
    if xcodebuild.returncode != 0:
        fail(
            f"{_XCODE_FULL_HINT}\n"
            f"  (현재 활성 개발자 디렉터리: {developer_dir})\n"
            f"  xcodebuild: {xcodebuild.stderr.strip() or xcodebuild.stdout.strip()}"
        )

    if shutil.which("pod") is None:
        fail(_COCOAPODS_HINT)

    xcode_version = xcodebuild.stdout.strip().splitlines()[0] if xcodebuild.stdout.strip() else "?"
    info(f"macOS 빌드 도구 확인됨: {xcode_version} ({developer_dir})")


def find_msvc_redist_crt_dir(base: Path | None = None) -> Path | None:
    """MSVC x64 CRT redist 폴더(가장 최신 버전). 없으면 None.

    ``<base>/<버전>/x64/Microsoft.VC*.CRT`` 구조에서 버전 문자열이 가장 큰 것을 고른다.
    """
    base = base or Path(_MSVC_REDIST_BASE)
    if not base.is_dir():
        return None
    candidates: list[tuple[list[int], Path]] = []
    for version_dir in base.iterdir():
        parts = version_dir.name.split(".")
        if not version_dir.is_dir() or not all(p.isdigit() for p in parts):
            continue
        for crt_dir in sorted((version_dir / "x64").glob("Microsoft.VC*.CRT")):
            candidates.append(([int(p) for p in parts], crt_dir))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def prepare_windows_crt(staging: Path, *, redist_crt_dir: Path | None) -> Path | None:
    """CRT DLL을 ``<staging>/System32``에 모아 두고 staging 경로를 돌려준다.

    빌드에서 ``WINDIR``을 이 경로로 바꿔 주면 플러그인이 여기서 x64 DLL을 가져간다.
    redist를 못 찾으면 None을 돌려 기존 동작(진짜 WINDIR)을 그대로 둔다.
    """
    if redist_crt_dir is None:
        return None
    target = staging / "System32"
    target.mkdir(parents=True, exist_ok=True)
    for name in WINDOWS_CRT_DLLS:
        source = redist_crt_dir / name
        if not source.is_file():
            fail(f"MSVC redist에 {name}이 없습니다: {redist_crt_dir}")
        shutil.copy2(source, target / name)
    return staging


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

    Windows·macOS 모두 패치된 템플릿을 넘긴다(scripts/flet_template.py). Windows는 러너
    진입점에서 Velopack 훅을 처리하고 첫 창을 앱 크기로 만들기 위해, macOS는 첫 창 크기
    (MainMenu.xib) 때문이다.
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


def resign_adhoc(app_bundle: Path, *, runner=run) -> None:
    """번들 전체를 ad-hoc으로 다시 서명한다(:func:`prune_bundle` 직후에 부른다).

    지운 ``.pod``는 프레임워크의 ``_CodeSignature/CodeResources``에 **봉인된 리소스**로
    등재되어 있다(실측: 심볼릭 링크 대상까지 기록되어 있다). 그래서 파일만 지우면 번들이
    ``a sealed resource is missing or invalid`` 상태가 된다. Apple Silicon은 실행되는 모든
    Mach-O에 최소 ad-hoc 서명을 요구하므로 깨진 봉인을 그대로 배포할 수 없다.

    Developer ID 서명이 아니라 재봉인이다 — 공증되지 않는다는 사실은 그대로다.
    """
    info("번들 ad-hoc 재서명(제거한 링크 때문에 깨진 봉인 복구)")
    code = runner(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], cwd=REPO_ROOT)
    if code != 0:
        fail(f"ad-hoc 재서명 실패(exit {code}): {app_bundle}")


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
        macos_sign = sign.velopack_sign_args_macos()
        if "--signAppIdentity" not in macos_sign:
            # vpk는 UpdateMac과 sq.version을 Contents/MacOS에 끼워 넣으므로, 우리가 먼저
            # 재서명해도 그 시점에 앱 봉인이 다시 깨진다. vpk 자신이 마지막에 다시 봉인하게
            # ad-hoc 식별자를 넘긴다(실측: 안 넘기면 설치본이 sealed resource 오류 상태다).
            macos_sign = ["--signAppIdentity", "-", *macos_sign]
        args += macos_sign
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
    out_dir: Path,
    runner=run,
) -> Path:
    """번들을 Velopack 설치기 + 업데이트 패키지로 만든다.

    기존 GitHub 릴리스를 **먼저 받아**(``vpk download github``) 그 위에 델타를 만든다.
    첫 릴리스거나 네트워크가 안 되면 델타 없이 전체 릴리스로 진행한다.
    """
    out = out_dir
    out.mkdir(parents=True, exist_ok=True)

    # 매 빌드는 빈 폴더에서 시작한다. 이전 실행의 산출물이 남아 있으면 vpk가 "이미 같은
    # 버전 릴리스가 있다"며 거부해 **두 번째 빌드부터 항상 실패한다**(실측). 델타 기준은
    # 바로 아래 vpk download가 GitHub에서 다시 받아 오므로 지워도 잃는 것이 없다.
    # 사람이 쓴 릴리스 노트는 vpk 산출물이 아니므로 보존한다.
    for path in out.iterdir():
        if path.name == RELEASE_NOTES:
            continue
        shutil.rmtree(path) if path.is_dir() else path.unlink()

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
    elif target == "macos":
        ensure_macos_toolchain()

    # pyproject.toml(SSOT)의 버전을 __init__.py에 반영한 뒤(flet build가 이 파일을 그대로
    # 복사해 번들에 담으므로 빌드 전에 최신이어야 한다) 빌드에 쓸 버전으로 쓴다.
    version = sync_version()

    # flet build의 진행 표시(rich)가 이모지를 stdout에 쓰는데 한국어 Windows 콘솔 기본
    # 코덱(cp949)으로는 인코딩할 수 없어 UnicodeEncodeError로 죽는다. 자식 Python을
    # UTF-8 모드로 강제해 회피한다(다른 OS엔 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    if target == "windows":
        # CRT를 공식 redist에서 가져가게 한다(위 WINDOWS_CRT_DLLS 주석 참고).
        # SystemRoot는 건드리지 않는다 — Windows API가 실제로 보는 값은 그쪽이다.
        crt = prepare_windows_crt(
            REPO_ROOT / "build" / "_crt", redist_crt_dir=find_msvc_redist_crt_dir()
        )
        if crt is not None:
            build_env["WINDIR"] = str(crt)
            info(f"CRT 스테이징: {crt} (WINDIR 재지정)")

    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])

    template_dir = flet_template.prepare(flet_version())
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
            resign_adhoc(pack_dir)
    out = velopack_pack(
        bundle_dir=pack_dir,
        version=version,
        target=target,
        vpk=find_vpk(),
        out_dir=velopack_output_dir(),
    )
    verify_velopack_output(out, target, version)
    info(f"Velopack 산출물: {out}")
    info(f"릴리스 업로드는 'python scripts/deploy.py'로 진행하세요 (태그 v{version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
