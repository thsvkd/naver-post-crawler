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


def test_collect_assets_are_scoped_to_version_and_channel(tmp_path: Path) -> None:
    # covers: Test-32
    out = tmp_path / "velopack"
    _populate(out)

    names = {p.name for p in deploy.collect_assets(out, "windows", "0.2.0")}

    assert "NaverPostCrawler-0.2.0-full.nupkg" in names
    assert "NaverPostCrawler-0.2.0-delta.nupkg" in names
    assert "releases.win.json" in names
    assert "NaverPostCrawler-win-Setup.exe" in names
    # 이전 버전과 다른 채널, 그리고 포터블은 빠진다.
    assert "NaverPostCrawler-0.1.9-full.nupkg" not in names
    assert "NaverPostCrawler-0.2.0-osx-full.nupkg" not in names
    assert "releases.osx.json" not in names
    assert not any(n.endswith("Portable.zip") for n in names)


def test_collect_assets_for_macos_channel(tmp_path: Path) -> None:
    # covers: Test-32
    out = tmp_path / "velopack"
    _populate(out)

    names = {p.name for p in deploy.collect_assets(out, "macos", "0.2.0")}

    assert "NaverPostCrawler-0.2.0-osx-full.nupkg" in names
    assert "releases.osx.json" in names
    assert "NaverPostCrawler-osx-Setup.pkg" in names
    assert "NaverPostCrawler-0.2.0-full.nupkg" not in names, "win 채널 nupkg가 섞이면 안 된다"


@pytest.mark.parametrize(
    "victim",
    [
        "NaverPostCrawler-win-Setup.exe",
        "NaverPostCrawler-0.2.0-full.nupkg",
        "releases.win.json",
    ],
)
def test_collect_assets_rejects_an_incomplete_build(tmp_path: Path, victim: str) -> None:
    """필수 산출물이 빠졌는데 올리면 그 플랫폼 사용자가 받을 파일이 없거나 피드가 없다."""
    out = tmp_path / "velopack"
    _populate(out)
    (out / victim).unlink()

    with pytest.raises(ValueError):
        deploy.collect_assets(out, "windows", "0.2.0")


def test_collect_assets_tolerates_a_missing_delta(tmp_path: Path) -> None:
    """델타는 직전 릴리스가 있을 때만 생긴다 — 첫 릴리스에 없는 게 정상이다."""
    out = tmp_path / "velopack"
    _populate(out)
    (out / "NaverPostCrawler-0.2.0-delta.nupkg").unlink()

    names = {p.name for p in deploy.collect_assets(out, "windows", "0.2.0")}

    assert "NaverPostCrawler-0.2.0-full.nupkg" in names


def test_create_release_pins_the_tag_to_the_built_commit() -> None:
    """태그를 방금 빌드한 커밋에 고정한다.

    --target을 빼면 gh는 원격 기본 브랜치의 tip에 태그를 만든다. 그 사이 다른 커밋이 올라와
    있으면 태그가 배포된 산출물과 다른 코드를 가리킨다. vpk upload에는 이 옵션이 없어서
    업로드를 gh로 옮겼다.
    """
    cmd = deploy.create_release_command(
        "v0.2.0",
        [Path("/tmp/a.nupkg")],
        notes_path=Path("/tmp/NOTES.md"),
        head_sha="abc123",
        publish=False,
    )

    assert cmd[:4] == ["gh", "release", "create", "v0.2.0"]
    assert cmd[cmd.index("--target") + 1] == "abc123"
    assert cmd[cmd.index("--notes-file") + 1] == str(Path("/tmp/NOTES.md"))


def test_create_release_stays_draft_unless_publish_is_requested() -> None:
    # covers: Test-34 (두 플랫폼 릴리스는 한쪽만 올라간 상태로 공개되면 안 된다)
    def build_cmd(publish: bool) -> list[str]:
        return deploy.create_release_command(
            "v0.2.0",
            [Path("/tmp/a.pkg")],
            notes_path=Path("/tmp/NOTES.md"),
            head_sha=None,
            publish=publish,
        )

    assert "--draft" in build_cmd(publish=False), (
        "먼저 올라간 플랫폼만으로 공개하면 다른 OS 사용자는 받을 파일이 없는 릴리스를 본다"
    )
    assert "--draft" not in build_cmd(publish=True)


def test_append_command_never_touches_the_release_notes() -> None:
    # covers: Test-34
    """두 번째 플랫폼은 에셋만 얹는다 — 첫 플랫폼이 쓴 본문을 덮어쓰면 안 된다."""
    cmd = deploy.append_assets_command("v0.2.0", [Path("/tmp/a.pkg"), Path("/tmp/b.json")])

    assert cmd[:4] == ["gh", "release", "upload", "v0.2.0"]
    assert "--notes-file" not in cmd
    assert "--notes" not in cmd
    assert "--clobber" in cmd, "업로드가 중간에 끊겼을 때 재실행으로 복구할 수 있어야 한다"


# -- 릴리스 계획(create/append 판정) ---------------------------------------------


def _plan(**overrides):
    kwargs = {
        "tag": "v0.2.0",
        "prev_tag": "v0.1.0",
        "existing_assets": None,
        "releases_json": "releases.win.json",
        "force": False,
        "tag_sha": "sha-head",
        "head_sha": "sha-head",
        "newest_tag": "v0.2.0",
    }
    kwargs.update(overrides)
    return deploy.plan_release(**kwargs)


def test_first_platform_creates_the_release() -> None:
    plan = _plan(existing_assets=None)

    assert plan.mode == "create"
    assert plan.error is None


def test_same_version_redeploy_is_blocked() -> None:
    # covers: Test-35
    """같은 버전을 다시 올리면 업데이트 피드가 어긋나 사용자가 영영 새 버전을 못 받는다."""
    plan = _plan(existing_assets=None, prev_tag="v0.2.0")

    assert plan.error is not None


def test_second_platform_appends_to_the_same_release() -> None:
    """정상 흐름: 첫 플랫폼이 만든 릴리스에 내 채널 에셋만 얹는다."""
    plan = _plan(existing_assets=["NaverPostCrawler-osx-Setup.pkg", "releases.osx.json"])

    assert plan.mode == "append"
    assert plan.error is None


def test_redeploying_the_same_channel_is_blocked() -> None:
    """내 채널 피드가 이미 올라가 있으면 이 플랫폼은 이미 배포된 것이다."""
    plan = _plan(existing_assets=["releases.win.json"])

    assert plan.error is not None


def test_stale_checkout_is_blocked() -> None:
    """과거 태그에 에셋을 붙이면 정작 latest에는 그 OS 설치기가 영영 없다."""
    plan = _plan(existing_assets=["releases.osx.json"], tag="v0.1.5", newest_tag="v0.2.0")

    assert plan.error is not None


def test_draft_release_counts_as_the_newest_tag() -> None:
    """배포는 draft로 만들어진다.

    최신 판정에서 draft를 빼면 두 번째 플랫폼이 정상 흐름인데도 자기가 만든 draft를 못 보고
    "최신 릴리스가 아니다"로 막힌다 — 그래서 newest_tag는 draft를 포함해 넘긴다.
    """
    plan = _plan(existing_assets=["releases.osx.json"], prev_tag="v0.1.0", newest_tag="v0.2.0")

    assert plan.error is None


def test_tag_pointing_at_a_different_commit_is_blocked() -> None:
    """아직 한 번도 릴리스된 적 없는 채널에는 다른 가드가 하나도 걸리지 않는다.

    버전을 안 올린 채 HEAD에서 빌드하면 그 버전이 아닌 코드가 그 버전으로 올라간다.
    """
    plan = _plan(existing_assets=["releases.osx.json"], tag_sha="sha-tag", head_sha="sha-head")

    assert plan.error is not None


def test_unverifiable_commit_is_blocked() -> None:
    """대조할 수 없으면 통과시키지 않는다 — 모르는 채 올리는 것이 막으려는 사고다."""
    assert _plan(existing_assets=["releases.osx.json"], tag_sha=None).error is not None
    assert _plan(existing_assets=["releases.osx.json"], head_sha=None).error is not None


def test_force_bypasses_every_release_gate() -> None:
    plan = _plan(
        existing_assets=["releases.win.json"],
        tag="v0.1.5",
        newest_tag="v0.2.0",
        tag_sha="sha-tag",
        force=True,
    )

    assert plan.mode == "append"
    assert plan.error is None


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


# -- uv.lock 버전 게이트 --------------------------------------------------------
# uv.lock은 자기 프로젝트의 버전도 기록한다. pyproject만 올려 커밋하면 락파일이 뒤처지고,
# 그 상태로 배포하면 build.py의 `uv sync`가 배포 도중에 락파일을 고쳐 워킹 트리를 더럽힌다.
# 그 시점은 위의 워킹 트리 게이트를 이미 통과한 뒤라 이번 배포는 나가고, 다음 배포가 영문
# 모를 "커밋되지 않은 변경"으로 막힌다.

_LOCK = """
version = 1

[[package]]
name = "some-dependency"
version = "9.9.9"

[[package]]
name = "naver-post-crawler"
version = "0.1.2"
source = { editable = "." }
"""


def test_lockfile_version_reads_own_entry_not_a_dependency() -> None:
    assert deploy.lockfile_version(_LOCK, "naver-post-crawler") == "0.1.2"


def test_lockfile_version_missing_package() -> None:
    assert deploy.lockfile_version(_LOCK, "not-in-lock") is None


def test_lockfile_version_broken_toml() -> None:
    assert deploy.lockfile_version("this is not toml {{{", "x") is None


def test_matching_lockfile_passes() -> None:
    assert deploy.check_lockfile_version("0.1.2", "0.1.2", force=False) is None


def test_stale_lockfile_is_blocked() -> None:
    error = deploy.check_lockfile_version("0.1.1", "0.1.2", force=False)

    assert error is not None
    assert "uv lock" in error


def test_unreadable_lockfile_is_blocked() -> None:
    assert deploy.check_lockfile_version(None, "0.1.2", force=False) is not None


def test_force_bypasses_lockfile_gate() -> None:
    assert deploy.check_lockfile_version("0.1.1", "0.1.2", force=True) is None
