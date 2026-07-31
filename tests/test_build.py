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


def test_flet_build_command_omits_template_on_macos() -> None:
    # covers: Test-10 (macOS 러너는 패치하지 않는다 — 넘기면 존재하지 않는 패치를 요구하게 된다)
    cmd = build.flet_build_command("macos", template_dir=None)

    assert "--template" not in cmd
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


def test_vpk_pack_args_macos_targets_app_bundle(tmp_path: Path) -> None:
    # covers: Test-11
    bundle = tmp_path / "Naver Post Crawler.app"
    bundle.mkdir()

    args = build.vpk_pack_args("macos", bundle_dir=bundle, version="0.2.0")

    assert _flag_value(args, "--channel") == "osx"
    assert _flag_value(args, "--packDir").endswith(".app")
    assert _flag_value(args, "--packId") == build.PACK_ID
    assert _flag_value(args, "--packVersion") == "0.2.0"
    # macOS에는 signtool 기반 --signParams가 존재하지 않는다.
    assert "--signParams" not in args


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
    bundle = tmp_path / "b"
    bundle.mkdir()

    win = build.vpk_pack_args("windows", bundle_dir=bundle, version="0.2.0")
    mac = build.vpk_pack_args("macos", bundle_dir=bundle, version="0.2.0")

    assert "--signParams" not in win
    assert "--signAppIdentity" not in mac


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
    bundle = tmp_path / ("bundle.app" if target == "macos" else "bundle")
    bundle.mkdir()
    calls: list[list[str]] = []

    def fake_runner(cmd: list[str], **_kwargs: object) -> int:
        calls.append(cmd)
        return 0

    build.velopack_pack(
        bundle_dir=bundle, version="0.2.0", target=target, vpk="vpk", runner=fake_runner
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
