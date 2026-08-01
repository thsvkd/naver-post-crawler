#!/usr/bin/env python3
"""릴리스 배포: 버전 게이트 → 빌드 → GitHub 릴리스 업로드.

Windows와 macOS 산출물은 **같은 태그 하나**에 함께 올린다. Velopack 채널이 OS별로 달라
(``win``/``osx``) 파일명이 겹치지 않고, 앱이 쓰는 ``GithubSource``는 최근 릴리스들을 훑어
자기 채널의 피드만 골라 읽기 때문이다. 태그를 나누면 그 조회 창을 두 배로 쓰게 된다.

플랫폼별 빌드 머신은 피할 수 없다(``flet build``·``vpk`` 양쪽 제약). 그래서 배포는 두 번
실행된다 — 예: Windows에서 한 번, macOS에서 한 번. 두 번째 실행은 첫 번째가 만든 릴리스에
**합류**만 하고(``--merge``) 릴리스 노트를 덮어쓰지 않는다.

사용:
    uv run python scripts/deploy.py --dry-run     # 올릴 에셋 목록만 보여주고 끝낸다
    uv run python scripts/deploy.py               # 빌드 + 업로드(draft 상태로 남는다)
    uv run python scripts/deploy.py --skip-build  # 이미 빌드된 dist/velopack을 올리기만 한다
    uv run python scripts/deploy.py --publish     # 두 플랫폼이 다 올라간 뒤 공개한다
    uv run python scripts/deploy.py --force       # 저장소 상태 게이트를 무시한다(복구용)

배포 전에 저장소 상태를 두 가지로 확인한다. 빌드는 **작업 트리의 파일**을 번들에 담는데
태그는 커밋을 가리키므로, 둘이 어긋나면 어느 커밋에도 없는 코드가 그 버전으로 배포된다.

    1. 미커밋 변경(추적되지 않는 파일 포함)이 없을 것.
    2. HEAD가 원격에 push되어 있을 것 — 이 스크립트는 push를 대신 하지 않는다.

절차:
    0. pyproject.toml의 [project].version(SSOT)을 미리 올려 둔다. 이전 릴리스와 같으면
       올리는 걸 잊은 것으로 보고 중단한다(Velopack은 같은 버전 재배포를 허용하지 않는다).
    1. scripts/build.py로 이 OS의 설치기를 만든다.
    2. 릴리스 노트를 준비한다 — **사람이 쓴다**(D-10). 기본 경로는 dist/velopack/RELEASE_NOTES.md
       이고, 이미 같은 태그의 릴리스가 있으면(두 번째 플랫폼) 노트를 넘기지 않는다.
    3. vpk upload github으로 이번 버전·이번 채널 에셋만 올린다. 기본은 **draft**다 —
       한쪽 플랫폼만 올라간 상태로 공개하면 다른 OS 사용자는 받을 파일이 없는 릴리스를 본다.
       두 플랫폼이 다 올라간 뒤 마지막 실행에 --publish를 준다.

사전 준비:
    - scripts/build.py와 동일(uv, Velopack CLI, 플랫폼별 툴체인).
    - gh CLI 로그인(`gh auth login`) — 기존 릴리스 조회에 쓴다.
    - GITHUB_TOKEN 또는 gh 자격증명(vpk upload가 쓴다).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from _common import REPO_ROOT, fail, info, pyproject_version

import build as build_script

_VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# 릴리스에 올릴 에셋의 확장자. Portable.zip은 올리지 않는다(설치기 + 업데이트 패키지 +
# 피드만 있으면 설치와 자동 업데이트가 모두 동작한다).
_ASSET_SUFFIXES = (".nupkg", ".exe", ".pkg")

# 알려진 Velopack 채널과 그중 기본 채널(파일명에 토큰이 붙지 않는 쪽).
_CHANNELS = ("win", "osx")
_DEFAULT_CHANNEL = "win"


def require_gh() -> None:
    if shutil.which("gh") is None:
        fail("gh(GitHub CLI)가 필요합니다. https://cli.github.com/ 를 참고하세요.")


def worktree_status() -> str | None:
    """``git status --porcelain`` 출력. 확인할 수 없으면 ``None``."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def head_commit() -> str | None:
    """지금 체크아웃된 커밋 SHA. 확인할 수 없으면 ``None``."""
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def remote_has_commit(sha: str) -> bool:
    """``sha`` 가 GitHub 쪽에 있는지(= push 됐는지).

    로컬의 ``@{u}`` 는 ``git fetch`` 전이면 낡아 있어 믿을 수 없으므로 원격에 직접 묻는다.
    """
    proc = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}", "--jq", ".sha"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def check_worktree_clean(porcelain_status: str | None, *, force: bool) -> str | None:
    """워킹 트리가 깨끗한지. 문제가 있으면 오류 메시지, 없으면 ``None``(순수 함수).

    빌드는 **작업 트리의 파일**을 그대로 번들에 담는데(flet은 src/를 복사한다) 태그는 커밋을
    가리킨다. 미커밋 변경이 있는 채로 배포하면 "그 버전이라고 이름 붙었지만 어느 커밋에도
    없는 코드"가 사용자에게 나가고, 나중에 그 버전을 재현할 수 없다. 추적되지 않는 파일도
    똑같이 번들에 들어가므로 함께 막는다.
    """
    if force:
        return None
    if porcelain_status is None:
        return (
            "git status를 확인하지 못했습니다 — 저장소 상태를 모르는 채로 배포할 수 없습니다"
            "(정말 강행하려면 --force)."
        )
    dirty = [line for line in porcelain_status.splitlines() if line.strip()]
    if not dirty:
        return None
    shown = "\n".join(f"    {line}" for line in dirty[:10])
    more = f"\n    … 외 {len(dirty) - 10}개" if len(dirty) > 10 else ""
    return (
        "커밋되지 않은 변경이 있습니다 — 빌드 산출물에는 들어가지만 태그가 가리키는 커밋에는 "
        "없는 코드가 배포됩니다.\n"
        f"{shown}{more}\n"
        "  커밋(필요하면 push)한 뒤 다시 실행하세요(정말 강행하려면 --force)."
    )


def check_head_pushed(commit: str | None, *, remote_has_head: bool, force: bool) -> str | None:
    """HEAD가 원격에 올라가 있는지. 문제가 있으면 오류 메시지, 없으면 ``None``(순수 함수).

    이 스크립트는 ``git push``를 하지 않는다(사용자의 브랜치를 말없이 밀어 올리는 건 이 도구가
    할 일이 아니다). 그런데 릴리스 태그는 원격에 있는 커밋만 가리킬 수 있으므로, push하지 않은
    채 배포하면 태그가 방금 빌드한 코드가 아니라 원격 기본 브랜치의 tip을 가리키게 된다.
    """
    if force:
        return None
    if commit is None:
        return "HEAD 커밋을 확인하지 못했습니다(git 저장소가 맞습니까?)."
    if not remote_has_head:
        return (
            f"현재 커밋({commit[:8]})이 GitHub에 없습니다 — 먼저 push하세요.\n"
            "  git push\n"
            "  (push하지 않으면 릴리스 태그가 이 커밋을 가리킬 수 없습니다.)"
        )
    return None


def latest_release_tag() -> str | None:
    """가장 최근 정식 릴리스 태그(v0.0.0 형식). 없으면 None."""
    proc = subprocess.run(
        ["gh", "release", "list", "--json", "tagName,isDraft,isPrerelease", "--limit", "100"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        fail(f"gh release list 실패: {proc.stderr.strip()}")
    for release in json.loads(proc.stdout or "[]"):  # gh는 최신순으로 돌려준다.
        if (
            not release["isDraft"]
            and not release["isPrerelease"]
            and _VERSION_TAG_RE.match(release["tagName"])
        ):
            return release["tagName"]
    return None


def release_exists(tag: str) -> bool:
    """이번 태그의 릴리스가 이미 있는지(draft 포함).

    두 번째 플랫폼에서의 배포인지 판정하는 근거다.
    """
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "tagName"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def assert_version_is_new(*, prev_tag: str | None, tag: str) -> None:
    """이전 정식 릴리스와 같은 버전이면 중단한다.

    같은 버전을 다시 올리면 업데이트 피드가 어긋나고, 사용자는 영영 새 버전을 못 받는다.
    """
    if prev_tag == tag:
        fail(
            f"버전이 이전 릴리스({tag})와 같습니다. "
            "pyproject.toml의 [project].version을 올린 뒤 다시 실행하세요."
        )


def _matches_channel(name: str, channel: str) -> bool:
    """파일 이름이 이 채널의 산출물인지 판정한다.

    Velopack은 **기본 채널(win)의 nupkg 이름에는 채널 토큰을 붙이지 않는다**
    (``<PackId>-<버전>-full.nupkg``). 반면 osx 산출물에는 ``-osx-``가 들어간다. 그래서
    판정이 대칭이 아니다 — 기본 채널은 "다른 채널 토큰이 없을 것"으로, 그 외 채널은
    "자기 토큰이 있을 것"으로 본다.
    """
    if channel == _DEFAULT_CHANNEL:
        return not any(f"-{other}-" in name for other in _CHANNELS if other != _DEFAULT_CHANNEL)
    return f"-{channel}-" in name


def upload_assets(out_dir: Path, *, version: str, channel: str) -> list[Path]:
    """이번 버전·이번 채널에 해당하는 업로드 대상 파일만 고른다.

    ``vpk download github``가 델타 계산 기준으로 **이전 릴리스 nupkg를 같은 폴더에 받아
    두기 때문에**, 확장자만 보고 전부 올리면 이전 버전을 다시 올리게 된다. 또 두 플랫폼이
    같은 폴더를 쓰는 경우(로컬에서 둘 다 만들어 본 경우) 다른 채널 파일이 섞일 수 있다.
    """
    if not out_dir.is_dir():
        fail(f"Velopack 산출 폴더가 없습니다: {out_dir} (먼저 빌드하세요)")

    selected: list[Path] = []
    for path in sorted(out_dir.iterdir()):
        if not path.is_file() or path.suffix not in _ASSET_SUFFIXES:
            continue
        if not _matches_channel(path.name, channel):
            continue
        if path.suffix == ".nupkg" and version not in path.name:
            # 델타 기준으로 받아 둔 이전 버전이다.
            continue
        selected.append(path)

    feed = out_dir / f"releases.{channel}.json"
    if not feed.is_file():
        fail(f"업데이트 피드가 없습니다: {feed} (vpk pack이 실패했을 수 있습니다)")
    selected.append(feed)
    return selected


def unwanted_release_assets(released: list[str], *, expected: list[str], channel: str) -> list[str]:
    """업로드 뒤 릴리스에서 지워야 할 에셋 이름.

    ``vpk upload``는 우리가 고른 목록이 아니라 outputDir의 ``assets.<channel>.json``
    인덱스를 보고 올린다. Windows에는 ``--noPortable``이 없어 Portable.zip이 항상 만들어지고
    함께 올라간다(실측). 설치기·업데이트 패키지·피드만 있으면 되므로 나머지는 정리한다.

    **이번 채널의 에셋만** 후보로 삼는다. 다른 플랫폼이 먼저 올려 둔 것과 레거시
    ``RELEASES``는 건드리지 않는다.
    """
    keep = set(expected)
    return [
        name
        for name in released
        if name not in keep and _matches_channel(name, channel) and f"-{channel}-" in name
    ]


def delete_asset_command(tag: str, name: str) -> list[str]:
    """릴리스 에셋 하나를 지우는 커맨드."""
    return ["gh", "release", "delete-asset", tag, name, "-y"]


def released_asset_names(tag: str) -> list[str]:
    """릴리스에 현재 올라가 있는 에셋 이름 목록."""
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets", "--jq", ".assets[].name"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def should_apply_notes(*, release_existed: bool) -> bool:
    """릴리스 본문을 이번 실행에서 설정해야 하는지.

    첫 플랫폼에서 한 번만 설정한다. 두 번째 플랫폼이 다시 쓰면 먼저 올라간 노트를 덮어쓴다.
    """
    return not release_existed


def notes_command(tag: str, notes_path: Path) -> list[str]:
    """릴리스 본문을 설정하는 커맨드.

    ``vpk upload github``에는 본문을 넣는 옵션이 없다(실측: ``--releaseName``만 있다).
    그래서 업로드가 만든 릴리스에 gh로 본문을 따로 채운다.
    """
    return ["gh", "release", "edit", tag, "--notes-file", str(notes_path)]


def github_token() -> str:
    """``vpk upload``에 넘길 GitHub 토큰. gh 로그인 자격증명을 재사용한다.

    vpk는 gh의 자격증명을 알아서 찾지 못하므로 명시적으로 넘겨야 한다.
    """
    proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    token = proc.stdout.strip()
    if proc.returncode != 0 or not token:
        fail("GitHub 토큰을 얻지 못했습니다. 'gh auth login'으로 먼저 로그인하세요.")
    return token


def upload_command(
    vpk: str,
    *,
    version: str,
    channel: str,
    out_dir: Path,
    token: str,
    publish: bool = False,
) -> list[str]:
    """``vpk upload github`` 커맨드.

    ``--merge``는 같은 태그의 기존 릴리스에 합류하게 한다(두 번째 플랫폼). 태그는 반드시
    명시한다 — 기본값은 ``v`` 없는 버전 문자열이라 이 저장소의 ``v0.1.0`` 관행과 어긋난다.

    ``publish``는 기본이 False다. 이 프로젝트는 **두 플랫폼이 같은 태그에 합류**하므로,
    한쪽만 올라간 상태로 공개하면 다른 OS 사용자는 받을 파일이 없는 릴리스를 보게 된다.
    두 플랫폼이 다 올라간 뒤 마지막 실행에서 ``--publish``로 공개한다.
    """
    return [
        vpk,
        "upload",
        "github",
        "--repoUrl",
        build_script.REPO_URL,
        "--channel",
        channel,
        "--outputDir",
        str(out_dir),
        "--tag",
        f"v{version}",
        "--token",
        token,
        # bool 옵션이지만 값을 받는 형태로 정의되어 있어 명시적으로 넘긴다.
        "--merge",
        "true",
        *(["--publish", "true"] if publish else []),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build", action="store_true", help="빌드를 건너뛰고 기존 산출물을 올린다."
    )
    parser.add_argument("--dry-run", action="store_true", help="올릴 에셋 목록만 출력하고 끝낸다.")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="릴리스를 공개한다. 기본은 draft — 두 플랫폼이 다 올라간 뒤 마지막 실행에서 준다.",
    )
    parser.add_argument(
        "--notes",
        type=Path,
        default=None,
        help="릴리스 노트 파일(기본: dist/velopack/RELEASE_NOTES.md). 사람이 작성한다.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="미커밋 변경·미push HEAD 검사를 무시한다(재실행 복구용).",
    )
    args = parser.parse_args()

    require_gh()

    # --dry-run은 빌드도 업로드도 하지 않으므로 아래 두 가드를 건너뛴다 — 무엇이 올라갈지만
    # 보려는 것뿐인데 커밋을 강요하면 쓸모가 없다.
    if not args.dry_run:
        error = check_worktree_clean(worktree_status(), force=args.force)
        if error:
            fail(error)
        commit = head_commit()
        error = check_head_pushed(
            commit,
            remote_has_head=remote_has_commit(commit) if commit else False,
            force=args.force,
        )
        if error:
            fail(error)
    version = pyproject_version()
    tag = f"v{version}"
    target = build_script.current_target()
    channel = build_script.channel_for(target)

    assert_version_is_new(prev_tag=latest_release_tag(), tag=tag)
    info(f"{tag} · {target}({channel} 채널) 배포")

    # --dry-run은 "무엇이 올라갈지"만 보는 용도다. 그것 때문에 수 분짜리 빌드를 돌리지 않는다.
    if not args.skip_build and not args.dry_run:
        info("빌드 시작 (scripts/build.py)")
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "build.py")], cwd=REPO_ROOT
        )
        if result.returncode != 0:
            fail(f"빌드 실패(exit {result.returncode})")

    out_dir = build_script.velopack_output_dir()
    assets = upload_assets(out_dir, version=version, channel=channel)
    info("업로드 대상:")
    for path in assets:
        info(f"  - {path.name}")

    if args.dry_run:
        info("--dry-run: 업로드하지 않고 종료합니다.")
        return 0

    exists = release_exists(tag)
    if exists:
        info(f"{tag} 릴리스가 이미 있습니다 — 에셋만 병합하고 릴리스 노트는 건드리지 않습니다.")
    notes_path = args.notes or (out_dir / "RELEASE_NOTES.md")
    if not exists and not notes_path.is_file():
        fail(
            f"릴리스 노트 파일이 없습니다: {notes_path}\n"
            "  릴리스 노트는 사람이 작성합니다. 파일을 만든 뒤 다시 실행하거나\n"
            "  --notes로 경로를 지정하세요."
        )

    cmd = upload_command(
        build_script.find_vpk(),
        version=version,
        channel=channel,
        out_dir=out_dir,
        token=github_token(),
        publish=args.publish,
    )
    info(f"GitHub 릴리스 업로드: {tag}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        fail(f"vpk upload 실패(exit {result.returncode})")

    # vpk가 인덱스를 보고 올린 군더더기(Portable.zip 등)를 정리한다.
    stale = unwanted_release_assets(
        released_asset_names(tag), expected=[p.name for p in assets], channel=channel
    )
    for name in stale:
        info(f"불필요한 에셋 제거: {name}")
        subprocess.run(delete_asset_command(tag, name), cwd=REPO_ROOT)

    if should_apply_notes(release_existed=exists):
        info(f"릴리스 본문 설정: {notes_path}")
        notes_result = subprocess.run(notes_command(tag, notes_path), cwd=REPO_ROOT)
        if notes_result.returncode != 0:
            fail(f"릴리스 본문 설정 실패(exit {notes_result.returncode})")
    if args.publish:
        info(f"완료(공개): {build_script.REPO_URL}/releases/tag/{tag}")
    else:
        info(
            f"완료(draft): {build_script.REPO_URL}/releases/tag/{tag}\n"
            "  다른 플랫폼 산출물까지 올린 뒤 --publish로 공개하세요."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
