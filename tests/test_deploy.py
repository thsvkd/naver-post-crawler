"""배포 스크립트(scripts/deploy.py)의 에셋 선별·태그·게이트 검증.

두 플랫폼 산출물을 **같은 태그 하나**에 올린다(채널이 달라 파일명이 겹치지 않는다).
그래서 배포는 두 머신에서 두 번 실행되고, 두 번째 실행이 첫 번째의 릴리스 노트를
덮어쓰거나 이전 버전 nupkg를 딸려 올리면 안 된다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[1]


def _load_deploy():
    spec = importlib.util.spec_from_file_location(
        "npc_deploy_script", _REPO_ROOT / "scripts" / "deploy.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy()


def _populate(out: Path) -> None:
    """vpk 산출 폴더를 흉내 낸다 — 이전 버전 nupkg가 함께 있는 상태가 핵심이다.

    ``vpk download github``가 델타 기준으로 이전 릴리스를 같은 폴더에 받아 두기 때문에,
    "*.nupkg를 전부 올린다"는 이전 버전을 다시 올리는 사고가 된다.
    """
    out.mkdir(parents=True, exist_ok=True)
    for name in (
        "NaverPostCrawler-0.1.9-full.nupkg",  # 이전 버전(win 채널)
        "NaverPostCrawler-0.2.0-full.nupkg",  # 이번 버전(win 채널)
        "NaverPostCrawler-0.2.0-delta.nupkg",
        "NaverPostCrawler-0.2.0-osx-full.nupkg",  # 이번 버전(osx 채널)
        "NaverPostCrawler-win-Setup.exe",
        "NaverPostCrawler-osx-Setup.pkg",
        "releases.win.json",
        "releases.osx.json",
        "NaverPostCrawler-0.2.0-win-Portable.zip",  # 업로드 대상이 아니다
    ):
        (out / name).write_text("", encoding="utf-8")


def test_upload_assets_are_scoped_to_version_and_channel(tmp_path: Path) -> None:
    # covers: Test-32
    out = tmp_path / "velopack"
    _populate(out)

    names = {p.name for p in deploy.upload_assets(out, version="0.2.0", channel="win")}

    assert "NaverPostCrawler-0.2.0-full.nupkg" in names
    assert "NaverPostCrawler-0.2.0-delta.nupkg" in names
    assert "releases.win.json" in names
    assert "NaverPostCrawler-win-Setup.exe" in names
    # 이전 버전과 다른 채널, 그리고 포터블은 빠진다.
    assert "NaverPostCrawler-0.1.9-full.nupkg" not in names
    assert "NaverPostCrawler-0.2.0-osx-full.nupkg" not in names
    assert "releases.osx.json" not in names
    assert not any(n.endswith("Portable.zip") for n in names)


def test_upload_assets_for_macos_channel(tmp_path: Path) -> None:
    # covers: Test-32
    out = tmp_path / "velopack"
    _populate(out)

    names = {p.name for p in deploy.upload_assets(out, version="0.2.0", channel="osx")}

    assert "NaverPostCrawler-0.2.0-osx-full.nupkg" in names
    assert "releases.osx.json" in names
    assert "NaverPostCrawler-osx-Setup.pkg" in names
    assert "NaverPostCrawler-0.2.0-full.nupkg" not in names, "win 채널 nupkg가 섞이면 안 된다"


def test_upload_command_passes_explicit_v_tag() -> None:
    # covers: Test-33
    cmd = deploy.upload_command("vpk", version="0.2.0", channel="win", out_dir=Path("/tmp/o"))

    assert "--tag" in cmd
    assert cmd[cmd.index("--tag") + 1] == "v0.2.0", (
        "도구 기본 태그는 'v' 없는 버전이라 기존 v0.1.0 관행과 어긋난다"
    )
    assert "--merge" in cmd, "두 플랫폼이 같은 릴리스에 합류해야 한다"


def test_upload_stays_draft_unless_publish_is_requested() -> None:
    # covers: Test-34 (두 플랫폼 릴리스는 한쪽만 올라간 상태로 공개되면 안 된다)
    draft = deploy.upload_command("vpk", version="0.2.0", channel="osx", out_dir=Path("/tmp/o"))
    published = deploy.upload_command(
        "vpk", version="0.2.0", channel="osx", out_dir=Path("/tmp/o"), publish=True
    )

    assert "--publish" not in draft, (
        "먼저 올라간 플랫폼만으로 공개하면 다른 OS 사용자는 받을 파일이 없는 릴리스를 본다"
    )
    assert "--publish" in published


def test_release_notes_are_generated_only_once(tmp_path: Path) -> None:
    # covers: Test-34
    notes = tmp_path / "RELEASE_NOTES.md"
    notes.write_text("첫 플랫폼이 만든 노트", encoding="utf-8")

    # 이미 릴리스가 있으면(두 번째 플랫폼) 노트를 넘기지 않는다 — 덮어쓰기 방지.
    assert deploy.notes_argument(notes, release_exists=True) == []
    assert deploy.notes_argument(notes, release_exists=False) == [
        "--releaseNotes",
        str(notes),
    ]


def test_same_version_redeploy_is_blocked() -> None:
    # covers: Test-35
    with pytest.raises(SystemExit):
        deploy.assert_version_is_new(prev_tag="v0.2.0", tag="v0.2.0")

    deploy.assert_version_is_new(prev_tag="v0.1.0", tag="v0.2.0")  # 통과한다
    deploy.assert_version_is_new(prev_tag=None, tag="v0.2.0")  # 첫 릴리스도 통과한다
