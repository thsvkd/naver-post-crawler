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
    cmd = deploy.upload_command(
        "vpk", version="0.2.0", channel="win", out_dir=Path("/tmp/o"), token="tok"
    )

    assert "--tag" in cmd
    assert cmd[cmd.index("--tag") + 1] == "v0.2.0", (
        "도구 기본 태그는 'v' 없는 버전이라 기존 v0.1.0 관행과 어긋난다"
    )
    assert cmd[cmd.index("--merge") + 1] == "true", "두 플랫폼이 같은 릴리스에 합류해야 한다"
    assert cmd[cmd.index("--token") + 1] == "tok", "vpk는 gh 자격증명을 알아서 쓰지 않는다"
    # vpk upload github에는 릴리스 본문을 넣는 옵션이 없다(실측: --releaseName만 있다).
    assert "--releaseNotes" not in cmd


def test_upload_stays_draft_unless_publish_is_requested() -> None:
    # covers: Test-34 (두 플랫폼 릴리스는 한쪽만 올라간 상태로 공개되면 안 된다)
    draft = deploy.upload_command(
        "vpk", version="0.2.0", channel="osx", out_dir=Path("/tmp/o"), token="t"
    )
    published = deploy.upload_command(
        "vpk", version="0.2.0", channel="osx", out_dir=Path("/tmp/o"), token="t", publish=True
    )

    assert "--publish" not in draft, (
        "먼저 올라간 플랫폼만으로 공개하면 다른 OS 사용자는 받을 파일이 없는 릴리스를 본다"
    )
    assert published[published.index("--publish") + 1] == "true"


def test_release_notes_are_applied_only_on_first_platform(tmp_path: Path) -> None:
    # covers: Test-34
    notes = tmp_path / "RELEASE_NOTES.md"
    notes.write_text("첫 플랫폼이 쓴 노트", encoding="utf-8")

    # 이미 릴리스가 있으면(두 번째 플랫폼) 본문을 건드리지 않는다 — 덮어쓰기 방지.
    assert deploy.should_apply_notes(release_existed=True) is False
    assert deploy.should_apply_notes(release_existed=False) is True

    # vpk에는 본문 옵션이 없으므로 gh로 따로 설정한다.
    cmd = deploy.notes_command("v0.2.0", notes)
    assert cmd[:4] == ["gh", "release", "edit", "v0.2.0"]
    assert cmd[cmd.index("--notes-file") + 1] == str(notes)


def test_same_version_redeploy_is_blocked() -> None:
    # covers: Test-35
    with pytest.raises(SystemExit):
        deploy.assert_version_is_new(prev_tag="v0.2.0", tag="v0.2.0")

    deploy.assert_version_is_new(prev_tag="v0.1.0", tag="v0.2.0")  # 통과한다
    deploy.assert_version_is_new(prev_tag=None, tag="v0.2.0")  # 첫 릴리스도 통과한다


def test_unwanted_assets_are_scoped_to_this_channel() -> None:
    # covers: Test-32
    # 실측: vpk upload는 outputDir의 assets.<channel>.json 인덱스를 보고 올리므로, 우리가
    # 고른 목록과 무관하게 Portable.zip까지 올라간다(Windows에는 --noPortable이 없다).
    # 업로드 뒤 정리해야 하는데, 다른 플랫폼이 먼저 올려 둔 에셋은 건드리면 안 된다.
    released = [
        "NaverPostCrawler-0.1.1-full.nupkg",
        "NaverPostCrawler-win-Setup.exe",
        "NaverPostCrawler-win-Portable.zip",
        "releases.win.json",
        "NaverPostCrawler-0.1.1-osx-full.nupkg",
        "NaverPostCrawler-osx-Setup.pkg",
        "releases.osx.json",
        "RELEASES",
    ]
    expected = [
        "NaverPostCrawler-0.1.1-full.nupkg",
        "NaverPostCrawler-win-Setup.exe",
        "releases.win.json",
    ]

    unwanted = deploy.unwanted_release_assets(released, expected=expected, channel="win")

    assert unwanted == ["NaverPostCrawler-win-Portable.zip"]


def test_unwanted_assets_never_touch_the_other_platform() -> None:
    # covers: Test-32
    released = ["NaverPostCrawler-osx-Setup.pkg", "releases.osx.json", "RELEASES"]

    unwanted = deploy.unwanted_release_assets(released, expected=[], channel="win")

    assert unwanted == [], "osx 에셋과 레거시 인덱스는 win 실행이 지우면 안 된다"


# -- 배포 전 저장소 상태 게이트 -------------------------------------------------
# 빌드는 **작업 트리의 파일**을 번들에 담는데 태그는 커밋을 가리킨다. 둘이 어긋나면 "그
# 버전이라고 이름 붙었지만 어느 커밋에도 없는 코드"가 배포되고 재현이 불가능해진다.
# 이 스크립트는 push를 대신 하지 않으므로, push되지 않은 커밋에서의 배포도 막아야 한다.


def test_clean_worktree_passes() -> None:
    assert deploy.check_worktree_clean("", force=False) is None
    assert deploy.check_worktree_clean("\n\n", force=False) is None


def test_dirty_worktree_is_blocked() -> None:
    error = deploy.check_worktree_clean(" M src/naver_post_crawler/cookie.py\n", force=False)

    assert error is not None
    assert "cookie.py" in error


def test_untracked_file_is_blocked() -> None:
    # 추적되지 않는 파일도 flet이 src/를 복사할 때 번들에 함께 들어간다.
    assert deploy.check_worktree_clean("?? src/naver_post_crawler/patch.py\n", force=False)


def test_unknown_git_status_is_blocked() -> None:
    # 조회 실패를 "깨끗함"으로 오해하면 게이트가 통째로 무력해진다.
    assert deploy.check_worktree_clean(None, force=False) is not None


def test_force_bypasses_worktree_gate() -> None:
    assert deploy.check_worktree_clean(" M x.py\n", force=True) is None
    assert deploy.check_worktree_clean(None, force=True) is None


def test_pushed_head_passes() -> None:
    assert deploy.check_head_pushed("abc1234", remote_has_head=True, force=False) is None


def test_unpushed_head_is_blocked() -> None:
    error = deploy.check_head_pushed("abc1234def", remote_has_head=False, force=False)

    assert error is not None
    assert "push" in error


def test_unknown_head_is_blocked() -> None:
    assert deploy.check_head_pushed(None, remote_has_head=False, force=False) is not None


def test_force_bypasses_push_gate() -> None:
    assert deploy.check_head_pushed("abc1234", remote_has_head=False, force=True) is None
