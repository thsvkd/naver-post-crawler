"""빌드 스크립트(scripts/build.py)의 인자·경로 규칙 검증.

``flet build``와 ``vpk``는 이 개발 머신에서 전부 돌려 볼 수 없다(Windows 빌드는 Windows
에서만 되고, 설치기 동작 확인은 실기 몫이다). 그래서 build.py는 "무엇을 실행할지 정하는
순수 함수"와 "그것을 실행하는 얇은 껍데기"로 나뉘어 있고, 여기서는 앞쪽만 잠근다.

여기서 막으려는 회귀는 조용한 것들이다 — 채널이 어긋나 업데이트 피드를 못 찾거나,
``--template``이 빠져 Velopack 훅 패치 없는 러너가 배포되거나, macOS 산출물이 비어 있는데
"폴더가 존재한다"는 이유로 통과하는 것.
"""

from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[1]


def _load_build():
    """scripts/build.py를 파일 경로로 로드한다(최상위 이름 ``build``와의 충돌 회피)."""
    spec = importlib.util.spec_from_file_location(
        "npc_build_script", _REPO_ROOT / "scripts" / "build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build()


# -- 타깃 매핑 ---------------------------------------------------------------------------


def test_target_mapping_from_platform() -> None:
    # covers: Test-9
    assert build.target_for("Windows") == "windows"
    assert build.target_for("Darwin") == "macos"


def test_target_mapping_rejects_unsupported_platform() -> None:
    # covers: Test-9 (Linux는 배포 대상이 아니다 — 조용히 windows로 떨어지면 안 된다)
    with pytest.raises(SystemExit):
        build.target_for("Linux")


# -- flet build 커맨드 -------------------------------------------------------------------


def test_flet_build_command_includes_template_on_windows() -> None:
    # covers: Test-10
    cmd = build.flet_build_command("windows", template_dir=Path("/tmp/tpl"))

    assert "--template" in cmd
    assert str(Path("/tmp/tpl")) in cmd
    assert "windows" in cmd


def test_flet_build_command_includes_template_on_macos() -> None:
    # covers: Test-10
    """macOS도 패치된 템플릿으로 빌드한다 — 첫 창 크기(MainMenu.xib) 패치가 여기 있다.

    예전에는 macOS 러너를 패치하지 않아 ``--template``을 뺐다. 지금은 두 러너를 모두
    패치하므로, 빠지면 macOS 앱이 800x600으로 떴다가 앱 크기로 줄어드는 깜빡임이 돌아온다.
    """
    cmd = build.flet_build_command("macos", template_dir=Path("/tmp/tpl"))

    assert "--template" in cmd
    assert str(Path("/tmp/tpl")) in cmd
    assert "macos" in cmd


# -- vpk pack 인자 -----------------------------------------------------------------------


def test_vpk_pack_args_windows_channel_and_main_exe(tmp_path: Path) -> None:
    # covers: Test-11
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    args = build.vpk_pack_args("windows", bundle_dir=bundle, version="0.2.0")

    assert _flag_value(args, "--channel") == "win"
    assert _flag_value(args, "--packDir") == str(bundle)
    assert _flag_value(args, "--mainExe") == build.APP_EXE_WINDOWS
    assert _flag_value(args, "--packId") == build.PACK_ID
    assert _flag_value(args, "--packVersion") == "0.2.0"


def _app_bundle(tmp_path: Path, executable: str = "naver-post-crawler") -> Path:
    """``flet build macos`` 산출물과 같은 모양의 최소 ``.app`` 번들을 만든다."""
    bundle = tmp_path / "naver-post-crawler.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / executable).write_text("", encoding="utf-8")
    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": executable})
    )
    return bundle


def test_vpk_pack_args_macos_targets_app_bundle(tmp_path: Path) -> None:
    # covers: Test-11
    bundle = _app_bundle(tmp_path)

    args = build.vpk_pack_args("macos", bundle_dir=bundle, version="0.2.0")

    assert _flag_value(args, "--channel") == "osx"
    assert _flag_value(args, "--packDir").endswith(".app")
    assert _flag_value(args, "--packId") == build.PACK_ID
    assert _flag_value(args, "--packVersion") == "0.2.0"
    # macOS에는 signtool 기반 --signParams가 존재하지 않는다.
    assert "--signParams" not in args


def test_vpk_pack_args_macos_passes_main_exe_from_bundle(tmp_path: Path) -> None:
    # covers: Test-11 (회귀: vpk는 --mainExe 기본값으로 packId를 쓴다)
    # 실측 실패: packId가 'NaverPostCrawler'인데 실제 바이너리는 'naver-post-crawler'라
    # vpk pack이 "Could not find main application executable"로 죽었다. 번들이 스스로
    # 밝히는 이름(Info.plist의 CFBundleExecutable)을 넘겨야 한다.
    bundle = _app_bundle(tmp_path, executable="naver-post-crawler")

    args = build.vpk_pack_args("macos", bundle_dir=bundle, version="0.2.0")

    assert _flag_value(args, "--mainExe") == "naver-post-crawler"
    assert _flag_value(args, "--mainExe") != build.PACK_ID


def test_macos_main_exe_falls_back_to_binary_in_bundle(tmp_path: Path) -> None:
    # covers: Test-11 (Info.plist를 못 읽어도 조용히 packId로 떨어지면 안 된다)
    bundle = tmp_path / "app.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "some-binary").write_text("", encoding="utf-8")

    assert build.macos_main_exe(bundle) == "some-binary"


def test_macos_main_exe_fails_when_bundle_has_no_executable(tmp_path: Path) -> None:
    # covers: Test-11
    bundle = tmp_path / "empty.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)

    with pytest.raises(SystemExit):
        build.macos_main_exe(bundle)


def test_vpk_pack_args_omit_signing_when_unset(tmp_path: Path, monkeypatch) -> None:
    # covers: Test-11 (미서명이 기본 — 서명 인자가 비어 있으면 아예 넣지 않는다)
    for name in (
        "NPC_SIGN_THUMBPRINT",
        "NPC_SIGN_PFX",
        "NPC_SIGN_APP_IDENTITY",
        "NPC_SIGN_INSTALL_IDENTITY",
        "NPC_SIGN_NOTARY_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    win = build.vpk_pack_args("windows", bundle_dir=tmp_path, version="0.2.0")
    mac = build.vpk_pack_args("macos", bundle_dir=_app_bundle(tmp_path), version="0.2.0")

    assert "--signParams" not in win
    # macOS는 ad-hoc 재봉인만 붙는다(Apple Silicon 최소 요건). 실제 인증서로 서명하거나
    # 공증하는 인자는 환경변수를 채우기 전까지 붙지 않는다.
    assert _flag_value(mac, "--signAppIdentity") == "-"
    assert "--signInstallIdentity" not in mac
    assert "--notaryProfile" not in mac


# -- 번들 정리: 바깥을 가리키는 심볼릭 링크 --------------------------------------------
# 실측 실패: flet build macos 산출물의 site-packages에 `.pod -> ~/.pub-cache/.../darwin`
# 심볼릭 링크가 남는데, 그 대상 안에 다시 같은 링크가 있어 트리 순회가 무한 재귀한다.
# vpk pack이 "path is too long"으로 죽었다. 애초에 빌드 머신 절대 경로라 사용자 머신에서는
# 깨진 링크이므로, 배포 번들에 있어서는 안 된다.


def test_prune_bundle_removes_symlinks_pointing_outside(tmp_path: Path) -> None:
    # covers: Test-12
    outside = tmp_path / "pub-cache"
    outside.mkdir()
    bundle = _app_bundle(tmp_path)
    site = bundle / "Contents" / "Resources" / "site-packages"
    site.mkdir(parents=True)
    escaping = site / ".pod"
    escaping.symlink_to(outside)
    keeper = site / "real.py"
    keeper.write_text("", encoding="utf-8")
    internal = site / "inside-link"
    internal.symlink_to(keeper)

    removed = build.prune_bundle(bundle)

    assert not escaping.is_symlink(), "번들 밖을 가리키는 링크는 제거되어야 한다"
    assert escaping in removed
    assert keeper.exists(), "실제 파일은 건드리면 안 된다"
    assert internal.is_symlink(), "번들 안을 가리키는 링크는 그대로 둔다"


def test_prune_bundle_is_noop_for_clean_bundle(tmp_path: Path) -> None:
    # covers: Test-12
    bundle = _app_bundle(tmp_path)

    assert build.prune_bundle(bundle) == []


def test_resign_adhoc_reseals_the_whole_bundle(tmp_path: Path) -> None:
    # covers: Test-12
    # 지운 .pod는 프레임워크의 _CodeSignature/CodeResources에 **봉인된 리소스**로 들어 있다.
    # 지우기만 하면 "a sealed resource is missing or invalid"가 되므로 다시 봉인해야 한다.
    bundle = _app_bundle(tmp_path)
    calls: list[list[str]] = []

    build.resign_adhoc(bundle, runner=lambda cmd, **_kw: calls.append(cmd) or 0)

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "codesign"
    assert "--force" in cmd and "--deep" in cmd
    assert cmd[cmd.index("--sign") + 1] == "-", "ad-hoc 서명은 식별자가 '-'다"
    assert str(bundle) in cmd


def test_resign_adhoc_fails_loudly(tmp_path: Path) -> None:
    # covers: Test-12 (재서명 실패를 삼키면 깨진 서명이 그대로 배포된다)
    bundle = _app_bundle(tmp_path)

    with pytest.raises(SystemExit):
        build.resign_adhoc(bundle, runner=lambda _cmd, **_kw: 1)


def test_vpk_pack_args_macos_defaults_to_adhoc_identity(tmp_path: Path, monkeypatch) -> None:
    # covers: Test-15
    # 실측: vpk가 UpdateMac과 sq.version을 Contents/MacOS에 끼워 넣으면서 앱 봉인이 다시
    # 깨진다. 우리가 먼저 재서명해도 소용없으므로, vpk 자신이 마지막에 다시 봉인하게 한다.
    # 이건 Developer ID 서명이 아니라 Apple Silicon이 요구하는 최소 조건이다.
    for name in ("NPC_SIGN_APP_IDENTITY", "NPC_SIGN_INSTALL_IDENTITY", "NPC_SIGN_NOTARY_PROFILE"):
        monkeypatch.delenv(name, raising=False)

    args = build.vpk_pack_args("macos", bundle_dir=_app_bundle(tmp_path), version="0.2.0")

    assert _flag_value(args, "--signAppIdentity") == "-"


def test_vpk_pack_args_macos_prefers_configured_identity(tmp_path: Path, monkeypatch) -> None:
    # covers: Test-15 (환경변수를 채우면 그 값이 ad-hoc 기본값을 이긴다)
    monkeypatch.setenv("NPC_SIGN_APP_IDENTITY", "Developer ID Application: thsvkd")

    args = build.vpk_pack_args("macos", bundle_dir=_app_bundle(tmp_path), version="0.2.0")

    assert _flag_value(args, "--signAppIdentity") == "Developer ID Application: thsvkd"
    assert args.count("--signAppIdentity") == 1, "ad-hoc 기본값과 중복되면 안 된다"


def _flag_value(args: list[str], flag: str) -> str:
    assert flag in args, f"{flag}가 인자에 없습니다: {args}"
    return args[args.index(flag) + 1]


# -- 산출물 검증 -------------------------------------------------------------------------


def test_verify_artifact_requires_app_bundle_on_macos(tmp_path: Path) -> None:
    # covers: Test-12
    dst = tmp_path / "out"
    (dst / "그냥파일").parent.mkdir(parents=True)
    (dst / "그냥파일").write_text("x", encoding="utf-8")

    # 비어 있지 않다는 것만으로 통과하면 안 된다 — .app이 있어야 한다.
    with pytest.raises(SystemExit):
        build.verify_artifact(dst, "macos")

    (dst / "Naver Post Crawler.app").mkdir()
    build.verify_artifact(dst, "macos")  # 이제 통과한다


def test_verify_artifact_requires_exe_on_windows(tmp_path: Path) -> None:
    # covers: Test-12
    dst = tmp_path / "out"
    dst.mkdir()

    with pytest.raises(SystemExit):
        build.verify_artifact(dst, "windows")

    (dst / "naver-post-crawler.exe").write_text("", encoding="utf-8")
    build.verify_artifact(dst, "windows")


# -- 델타 전제조건: download가 pack보다 먼저, 같은 채널 --------------------------------


@pytest.mark.parametrize(("target", "channel"), [("windows", "win"), ("macos", "osx")])
def test_vpk_download_precedes_pack_with_matching_channel(
    tmp_path: Path, target: str, channel: str
) -> None:
    # covers: Test-13
    # 두 타깃 모두 확인한다 — windows만 보면 채널을 "win"으로 하드코딩해도 통과한다.
    if target == "macos":
        bundle = _app_bundle(tmp_path)
    else:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
    calls: list[list[str]] = []

    def fake_runner(cmd: list[str], **_kwargs: object) -> int:
        calls.append(cmd)
        return 0

    build.velopack_pack(
        bundle_dir=bundle,
        version="0.2.0",
        target=target,
        vpk="vpk",
        out_dir=tmp_path / "out",
        runner=fake_runner,
    )

    assert len(calls) >= 2, f"download와 pack이 모두 실행되어야 한다: {calls}"
    download_cmd, pack_cmd = calls[0], calls[1]
    assert "download" in download_cmd, f"첫 명령이 vpk download여야 한다: {download_cmd}"
    assert "pack" in pack_cmd, f"두 번째 명령이 vpk pack이어야 한다: {pack_cmd}"
    assert _flag_value(download_cmd, "--channel") == channel
    assert _flag_value(download_cmd, "--channel") == _flag_value(pack_cmd, "--channel"), (
        "델타는 같은 채널의 이전 릴리스를 기준으로만 만들어진다"
    )


def test_repo_url_comes_from_app_module() -> None:
    # covers: Test-16 (저장소 URL 단일 출처 — 빌드 스크립트가 앱 모듈 값을 읽는다)
    from naver_post_crawler import velopack_update

    assert build.REPO_URL == velopack_update.REPO_URL


def test_velopack_pack_clears_stale_local_artifacts(tmp_path: Path) -> None:
    # covers: Test-13
    # 실측 버그: 같은 버전을 두 번 빌드하면 vpk가 "There is a release in channel osx which is
    # equal or greater to the current version"으로 거부한다. 이전 pack 산출물이 출력 폴더에
    # 남아 있기 때문이다. 델타 기준은 vpk download가 GitHub에서 다시 받아 오므로, 매 빌드는
    # 빈 폴더에서 시작해야 재현 가능하다.
    out = tmp_path / "velopack"
    out.mkdir()
    stale = out / "NaverPostCrawler-0.2.0-osx-full.nupkg"
    stale.write_text("이전 실행 잔재", encoding="utf-8")
    order: list[str] = []

    def fake_runner(cmd: list[str], **_kwargs: object) -> int:
        # download가 도는 시점에는 이미 폴더가 비어 있어야 한다.
        order.append("download" if "download" in cmd else "pack")
        if "download" in cmd:
            assert not stale.exists(), "잔재를 지우기 전에 download를 돌리면 안 된다"
        return 0

    build.velopack_pack(
        bundle_dir=_app_bundle(tmp_path),
        version="0.2.0",
        target="macos",
        vpk="vpk",
        out_dir=out,
        runner=fake_runner,
    )

    assert order == ["download", "pack"]
    assert not stale.exists()


def test_velopack_pack_keeps_release_notes(tmp_path: Path) -> None:
    # covers: Test-13 (사람이 쓴 릴리스 노트를 빌드가 지워버리면 안 된다)
    out = tmp_path / "velopack"
    out.mkdir()
    notes = out / "RELEASE_NOTES.md"
    notes.write_text("사람이 쓴 노트", encoding="utf-8")
    build.velopack_pack(
        bundle_dir=_app_bundle(tmp_path),
        version="0.2.0",
        target="macos",
        vpk="vpk",
        out_dir=out,
        runner=lambda _cmd, **_kw: 0,
    )

    assert notes.is_file()
    assert notes.read_text(encoding="utf-8") == "사람이 쓴 노트"


# -- Windows CRT 스테이징 ----------------------------------------------------------------
# 실측 실패: serious_python_windows 플러그인의 CMakeLists가 CRT를 `$ENV{WINDIR}/System32`
# 에서 가져오는데, VS가 주는 cmake.exe가 32비트라 그 경로가 WOW64로 SysWOW64에 리다이렉트
# 된다. vcruntime140_1.dll은 **x64 전용**이라 SysWOW64에는 존재할 수 없어 빌드가 죽는다.
# 공식 MSVC redist 폴더에서 x64 DLL을 모아 두고 WINDIR을 그쪽으로 돌린다.


def test_prepare_windows_crt_stages_x64_dlls(tmp_path: Path) -> None:
    # covers: Test-12
    redist = tmp_path / "redist" / "x64" / "Microsoft.VC143.CRT"
    redist.mkdir(parents=True)
    for name in build.WINDOWS_CRT_DLLS:
        (redist / name).write_text(name, encoding="utf-8")
    staging = tmp_path / "crt"

    result = build.prepare_windows_crt(staging, redist_crt_dir=redist)

    assert result == staging
    for name in build.WINDOWS_CRT_DLLS:
        staged = staging / "System32" / name
        assert staged.is_file(), f"{name}이 스테이징되지 않았다"
        assert staged.read_text(encoding="utf-8") == name


def test_prepare_windows_crt_returns_none_without_redist(tmp_path: Path) -> None:
    # covers: Test-12 (redist를 못 찾으면 기존 동작을 그대로 둔다 — WINDIR을 건드리지 않는다)
    assert build.prepare_windows_crt(tmp_path / "crt", redist_crt_dir=None) is None


def test_prepare_windows_crt_fails_when_dll_missing(tmp_path: Path) -> None:
    # covers: Test-12 (일부만 복사해 두면 빌드가 더 뒤에서 알 수 없는 이유로 죽는다)
    redist = tmp_path / "redist"
    redist.mkdir()
    (redist / build.WINDOWS_CRT_DLLS[0]).write_text("x", encoding="utf-8")

    with pytest.raises(SystemExit):
        build.prepare_windows_crt(tmp_path / "crt", redist_crt_dir=redist)


def test_find_msvc_redist_crt_dir_picks_newest_x64(tmp_path: Path) -> None:
    # covers: Test-12
    base = tmp_path / "MSVC"
    for version in ("14.40.33807", "14.44.35112"):
        (base / version / "x64" / "Microsoft.VC143.CRT").mkdir(parents=True)
    (base / "v143").mkdir()  # 버전이 아닌 항목은 무시한다

    found = build.find_msvc_redist_crt_dir(base)

    assert found is not None
    assert "14.44.35112" in str(found)
    assert found.name == "Microsoft.VC143.CRT"


# -- macOS 사전 점검 ---------------------------------------------------------------------
# CLT만 깔린 맥에서 실제로 나온 출력(yt-knowledge-extractor에서 실측).
_CLT_DIR = "/Library/Developer/CommandLineTools"
_XCODEBUILD_CLT_ERROR = (
    "xcode-select: error: tool 'xcodebuild' requires Xcode, but active developer "
    f"directory '{_CLT_DIR}' is a command line tools instance"
)
_XCODE_DIR = "/Applications/Xcode.app/Contents/Developer"
_XCODEBUILD_OK = "Xcode 16.2\nBuild version 16C5032a"


def _fake_macos_env(monkeypatch, *, developer_dir: str, xcodebuild_ok: bool, tools: set) -> None:
    """지정한 맥 환경을 흉내 내도록 subprocess.run / shutil.which를 갈아 끼운다."""
    import subprocess as _sp

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "xcode-select":
            return _sp.CompletedProcess(cmd, 0, developer_dir + "\n", "")
        if cmd[0] == "xcodebuild":
            if xcodebuild_ok:
                return _sp.CompletedProcess(cmd, 0, _XCODEBUILD_OK, "")
            return _sp.CompletedProcess(cmd, 1, "", _XCODEBUILD_CLT_ERROR)
        raise AssertionError(f"예상치 못한 명령: {cmd}")

    monkeypatch.setattr(build.subprocess, "run", fake_run)
    monkeypatch.setattr(
        build.shutil, "which", lambda tool: f"/usr/bin/{tool}" if tool in tools else None
    )
    monkeypatch.setattr(build, "info", lambda message: None)


def test_macos_toolchain_rejects_command_line_tools_only(monkeypatch) -> None:
    """CLT 전용 머신: xcode-select도 vpk용 명령도 다 통과하지만 빌드는 불가능하다.

    이 구분을 놓치면 Flutter SDK를 다 받고 몇 분 지나서야 "Xcode installation is
    incomplete"로 죽는다 — 원인이 환경 문제라는 게 한참 뒤에 드러난다.
    """
    _fake_macos_env(
        monkeypatch,
        developer_dir=_CLT_DIR,
        xcodebuild_ok=False,
        tools={*build._MACOS_TOOLS, "pod"},
    )
    with pytest.raises(SystemExit):
        build.ensure_macos_toolchain()


def test_macos_toolchain_rejects_missing_cocoapods(monkeypatch) -> None:
    """전체 Xcode가 있어도 CocoaPods가 없으면 Flutter 플러그인 단계에서 죽는다."""
    _fake_macos_env(
        monkeypatch,
        developer_dir=_XCODE_DIR,
        xcodebuild_ok=True,
        tools=set(build._MACOS_TOOLS),  # pod 없음
    )
    with pytest.raises(SystemExit):
        build.ensure_macos_toolchain()


def test_macos_toolchain_rejects_missing_velopack_tool(monkeypatch) -> None:
    """vpk가 .pkg를 만들며 직접 부르는 명령이 하나라도 없으면 중단한다."""
    _fake_macos_env(
        monkeypatch,
        developer_dir=_XCODE_DIR,
        xcodebuild_ok=True,
        tools={*(t for t in build._MACOS_TOOLS if t != "pkgbuild"), "pod"},
    )
    with pytest.raises(SystemExit):
        build.ensure_macos_toolchain()


def test_macos_toolchain_accepts_full_xcode_with_cocoapods(monkeypatch) -> None:
    """정상 환경은 통과해야 한다 — 점검이 과하게 조여 빌드를 막으면 안 된다."""
    _fake_macos_env(
        monkeypatch,
        developer_dir=_XCODE_DIR,
        xcodebuild_ok=True,
        tools={*build._MACOS_TOOLS, "pod"},
    )
    build.ensure_macos_toolchain()  # 예외 없이 끝나야 한다.


# -- 산출물 이름 검증 --------------------------------------------------------------------


def test_nupkg_glob_omits_channel_suffix_only_on_windows() -> None:
    """win/osx 글롭은 서로의 파일을 절대 매치하면 안 된다.

    두 OS 산출물을 같은 태그에 올리므로, 이름이 겹치면 나중에 올린 쪽이 앞선 것을 덮어쓴다.
    Velopack이 접미사를 빼는 조합은 Windows 타깃 + win 채널뿐이다.
    """
    assert build.full_nupkg_glob("windows", "1.2.3") == "*-1.2.3-full.nupkg"
    assert build.full_nupkg_glob("macos", "1.2.3") == "*-1.2.3-osx-full.nupkg"

    import fnmatch

    win_file = "NaverPostCrawler-1.2.3-full.nupkg"
    osx_file = "NaverPostCrawler-1.2.3-osx-full.nupkg"
    assert not fnmatch.fnmatch(osx_file, build.full_nupkg_glob("windows", "1.2.3"))
    assert not fnmatch.fnmatch(win_file, build.full_nupkg_glob("macos", "1.2.3"))


def test_feed_and_installer_names_are_per_target() -> None:
    assert build.releases_json_name("windows") == "releases.win.json"
    assert build.releases_json_name("macos") == "releases.osx.json"
    assert build.setup_glob("windows") == "*-Setup.exe"
    assert build.setup_glob("macos") == "*-Setup.pkg"


def _write_velopack_output(out: Path, *, target: str, version: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    suffix = "" if target == "windows" else "-osx"
    ext = "exe" if target == "windows" else "pkg"
    (out / f"NaverPostCrawler{suffix}-Setup.{ext}").write_text("x", encoding="utf-8")
    (out / f"NaverPostCrawler-{version}{suffix}-full.nupkg").write_text("x", encoding="utf-8")
    (out / build.releases_json_name(target)).write_text("{}", encoding="utf-8")


def test_verify_velopack_output_passes_on_complete_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(build, "info", lambda message: None)
    for target in ("windows", "macos"):
        out = tmp_path / target
        _write_velopack_output(out, target=target, version="1.2.3")
        build.verify_velopack_output(out, target, "1.2.3")  # 예외 없이 끝나야 한다.


@pytest.mark.parametrize("missing", ["installer", "feed", "nupkg"])
def test_verify_velopack_output_fails_when_a_required_file_is_missing(
    tmp_path: Path, monkeypatch, missing: str
) -> None:
    """하나라도 빠지면 중단해야 한다 — 빌드 성공으로 넘어가면 업로드가 조용히 누락된다."""
    monkeypatch.setattr(build, "info", lambda message: None)
    out = tmp_path / "velopack"
    _write_velopack_output(out, target="macos", version="1.2.3")
    victim = {
        "installer": "NaverPostCrawler-osx-Setup.pkg",
        "feed": "releases.osx.json",
        "nupkg": "NaverPostCrawler-1.2.3-osx-full.nupkg",
    }[missing]
    (out / victim).unlink()

    with pytest.raises(SystemExit):
        build.verify_velopack_output(out, "macos", "1.2.3")


def test_verify_velopack_output_rejects_wrong_channel_nupkg(tmp_path: Path, monkeypatch) -> None:
    """win 이름의 nupkg만 있는 폴더를 osx 빌드 결과로 통과시키면 안 된다."""
    monkeypatch.setattr(build, "info", lambda message: None)
    out = tmp_path / "velopack"
    out.mkdir()
    (out / "NaverPostCrawler-osx-Setup.pkg").write_text("x", encoding="utf-8")
    (out / "releases.osx.json").write_text("{}", encoding="utf-8")
    (out / "NaverPostCrawler-1.2.3-full.nupkg").write_text("x", encoding="utf-8")  # win 이름

    with pytest.raises(SystemExit):
        build.verify_velopack_output(out, "macos", "1.2.3")
