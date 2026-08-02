#!/usr/bin/env python3
"""릴리스 배포: 버전 게이트 → 빌드 → GitHub 릴리스 업로드.

Windows와 macOS 산출물은 **같은 태그 하나**에 함께 올린다. Velopack 채널이 OS별로 달라
(``win``/``osx``) 파일명이 겹치지 않고, 앱이 쓰는 ``GithubSource``는 최근 릴리스들을 훑어
자기 채널의 피드만 골라 읽기 때문이다. 태그를 나누면 그 조회 창을 두 배로 쓰게 된다.

플랫폼별 빌드 머신은 피할 수 없다(``flet build``·``vpk`` 양쪽 제약). 그래서 배포는 두 번
실행된다 — 예: Windows에서 한 번, macOS에서 한 번. 두 번째 실행은 첫 번째가 만든 릴리스에
자기 채널 에셋만 **덧붙이고** 릴리스 노트는 건드리지 않는다.

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
    2. 릴리스 노트를 준비한다. 기본 경로는 dist/velopack/RELEASE_NOTES.md 다.
       **파일이 없을 때만** 커밋 로그로 초안을 만들어 그 파일에 써 둔다(claude -p,
       scripts/release_notes_guide.md 지침). 릴리스는 draft로 만들어지므로 공개 전에 사람이
       읽고 고친다 — 자동 생성은 빈 화면에서 시작하지 않게 해 줄 뿐 최종본이 아니다.
       파일이 이미 있으면 손대지 않는다. 같은 태그의 릴리스가 이미 있으면(두 번째 플랫폼)
       노트를 아예 넘기지 않는다.
    3. gh release로 이번 버전·이번 채널 에셋만 올린다. 기본은 **draft**다 —
       한쪽 플랫폼만 올라간 상태로 공개하면 다른 OS 사용자는 받을 파일이 없는 릴리스를 본다.
       두 플랫폼이 다 올라간 뒤 마지막 실행에 --publish를 준다.

업로드에 ``vpk upload github``이 아니라 ``gh``를 쓰는 이유:
    - **태그를 방금 빌드한 커밋에 고정할 수 있다**(``gh release create --target <HEAD>``).
      vpk에는 그 옵션이 없어 태그가 원격 기본 브랜치의 tip에 붙는다. 위 2번 게이트를 통과한
      뒤 누군가 push하면, 태그가 배포된 산출물과 다른 코드를 가리키게 된다.
    - **올릴 파일을 명시한다.** vpk는 우리가 고른 목록이 아니라 outputDir의
      ``assets.<channel>.json`` 인덱스를 보고 올려서, Windows에서 항상 만들어지는
      Portable.zip이 딸려 올라갔다(그래서 올린 뒤 지우는 뒷정리가 따로 필요했다).

사전 준비:
    - scripts/build.py와 동일(uv, Velopack CLI, 플랫폼별 툴체인).
    - gh CLI 로그인(`gh auth login`) — 릴리스 조회·생성·업로드에 모두 쓴다.
    - claude CLI 로그인 — 릴리스 노트 **초안 생성에만** 쓴다. 노트를 직접 써서
      dist/velopack/RELEASE_NOTES.md 에 두면 필요 없다.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from _common import REPO_ROOT, check, fail, info, pyproject_version

import build as build_script

_VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_GUIDE_PATH = REPO_ROOT / "scripts" / "release_notes_guide.md"
_CLAUDE_TIMEOUT = 300  # 커밋 로그 요약이라 5분이면 충분히 여유 있다.


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


def tag_commit(tag: str) -> str | None:
    """``tag``가 가리키는 커밋 SHA. 확인할 수 없으면 ``None``.

    로컬 git이 아니라 GitHub 쪽에 묻는다. 첫 번째 플랫폼이 만든 태그는 두 번째 플랫폼의
    로컬 저장소에 ``git fetch`` 전까지 존재하지 않아서, 로컬만 보면 "태그를 못 찾음"을
    "커밋이 다름"으로 오판한다. ``commits/<ref>`` 엔드포인트는 경량·주석 태그를 모두
    커밋으로 풀어 주므로 태그 종류를 따질 필요가 없다.
    """
    proc = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{tag}", "--jq", ".sha"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def project_name() -> str:
    """``pyproject.toml`` 의 ``[project].name``. uv.lock 에서 자기 항목을 찾는 데 쓴다."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        return data["project"]["name"]
    except KeyError:
        fail("pyproject.toml에서 [project].name을 찾지 못했습니다.")


def lockfile_version(lock_text: str, name: str) -> str | None:
    """``uv.lock``에 적힌 **이 프로젝트 자신의** 버전. 못 찾으면 ``None``(순수 함수)."""
    try:
        data = tomllib.loads(lock_text)
    except tomllib.TOMLDecodeError:
        return None
    for package in data.get("package", []):
        if package.get("name") == name:
            version = package.get("version")
            return str(version) if version else None
    return None


def check_lockfile_version(
    lock_version: str | None, project_version: str, *, force: bool
) -> str | None:
    """``uv.lock``이 pyproject 버전을 따라왔는지. 어긋나면 오류 메시지(순수 함수).

    uv.lock은 자기 프로젝트의 버전도 기록한다. pyproject만 올려 커밋하면 락파일이 한 버전
    뒤처지고, 그 상태로 배포하면 build.py의 ``uv sync``가 **배포 도중에** 락파일을 고쳐 워킹
    트리를 더럽힌다. 그 시점은 :func:`check_worktree_clean`을 이미 통과한 뒤라 이번 배포는
    그대로 나가고, **다음 배포가 영문 모를 "커밋되지 않은 변경"으로 막힌다**
    (yt-knowledge-extractor의 v0.1.5 Windows 배포에서 실제로 겪었다).

    그래서 빌드 전에 여기서 끊고 무엇을 하면 되는지 알려 준다.
    """
    if force:
        return None
    if lock_version is None:
        return (
            "uv.lock에서 이 프로젝트의 버전을 읽지 못했습니다. 락파일이 깨졌는지 확인하세요"
            "(정말 강행하려면 --force)."
        )
    if lock_version != project_version:
        return (
            f"uv.lock의 버전({lock_version})이 pyproject.toml({project_version})과 다릅니다.\n"
            "  uv lock을 돌려 락파일을 맞추고 함께 커밋한 뒤 다시 실행하세요.\n"
            "  (그대로 두면 빌드 중 uv sync가 락파일을 고쳐 워킹 트리가 더러워집니다.)"
        )
    return None


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


def latest_release_tag(*, include_drafts: bool = False) -> str | None:
    """가장 최근 앱 버전 릴리스 태그(v0.0.0 형식). 없으면 None.

    draft는 **용도에 따라 갈린다** — 그래서 인자가 있다.

    - ``include_drafts=False``(기본): 릴리스 노트의 기준점이자 "버전을 올렸는가" 판정용.
      아직 공개되지 않은 draft는 기준이 될 수 없다.
    - ``include_drafts=True``: "지금 올리려는 태그가 최신인가" 판정용. 배포는 draft로
      만들어지므로, 여기서 draft를 빼면 **두 번째 플랫폼이 정상 흐름인데도** 자기가 만든
      draft를 못 보고 "최신 릴리스가 아니다"로 중단된다.
    """
    proc = subprocess.run(
        ["gh", "release", "list", "--json", "tagName,isDraft,isPrerelease", "--limit", "100"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        fail(f"gh release list 실패: {proc.stderr.strip()}")
    for release in json.loads(proc.stdout or "[]"):  # gh는 최신순으로 돌려준다.
        if release["isPrerelease"] or not _VERSION_TAG_RE.match(release["tagName"]):
            continue
        if release["isDraft"] and not include_drafts:
            continue
        return release["tagName"]
    return None


def release_assets(tag: str) -> list[str] | None:
    """``tag`` 릴리스에 이미 올라간 에셋 '이름' 목록. 릴리스가 없으면 ``None``.

    "릴리스 없음"과 "조회 실패"를 반드시 구분한다. 네트워크·권한 실패를 릴리스 없음으로
    오해하면 두 번째 플랫폼 실행이 ``gh release create``로 넘어가는데, 그건 이미 있는
    태그에 실패하거나(운이 좋으면) 첫 플랫폼이 쓴 릴리스 노트를 망친다.
    """
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "release not found" in stderr.lower():
            return None
        fail(f"gh release view {tag} 실패: {stderr}")
    data = json.loads(proc.stdout or "{}")
    return [asset["name"] for asset in data.get("assets", [])]


def commit_log_since(prev_tag: str | None) -> str:
    """prev_tag 이후(없으면 전체 히스토리) 커밋의 제목+본문을 최신순으로 모은다."""
    rev_range = f"{prev_tag}..HEAD" if prev_tag else "HEAD"
    proc = subprocess.run(
        ["git", "log", rev_range, "--no-merges", "--pretty=format:- %s%n%b%n---"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        fail(f"git log 실패: {proc.stderr.strip()}")
    return proc.stdout.strip()


def generate_release_notes(prev_tag: str | None, tag: str, commit_log: str) -> str:
    """scripts/release_notes_guide.md 지침대로 ``claude -p``를 호출해 노트 초안을 만든다.

    **초안일 뿐이다.** 릴리스는 어차피 draft로 만들어지므로 공개 전에 사람이 읽고 고친다.
    노트 파일이 이미 있으면 이 함수는 호출되지 않는다 — 사람이 쓴 글을 자동 생성으로
    덮어쓰는 일은 없어야 한다.

    ``--tools ""``로 도구를 막고 ``--setting-sources ""``로 사용자 설정을 배제한다. 릴리스
    노트를 쓰는 데 파일을 읽거나 명령을 실행할 이유가 없고, 개인 설정에 따라 결과가 달라지면
    같은 커밋 로그로 매번 다른 노트가 나온다.
    """
    if shutil.which("claude") is None:
        fail(
            "claude CLI를 찾을 수 없습니다. https://claude.com/claude-code 를 설치·로그인하거나,\n"
            "  릴리스 노트를 직접 작성해 dist/velopack/RELEASE_NOTES.md 에 두세요."
        )
    guide = _GUIDE_PATH.read_text(encoding="utf-8")
    user_prompt = (
        f"이전 릴리스: {prev_tag or '없음(첫 릴리스)'}\n"
        f"이번 릴리스: {tag}\n\n"
        f"커밋 로그:\n{commit_log}\n"
    )
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--system-prompt",
        guide,
        "--tools",
        "",
        "--no-session-persistence",
        "--setting-sources",
        "",
    ]
    info("릴리스 노트 초안 생성 중 (claude -p)…")
    try:
        proc = subprocess.run(
            cmd,
            input=user_prompt,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        fail(f"claude -p 응답 시간 초과({_CLAUDE_TIMEOUT}초)")

    data: dict | None = None
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
    if proc.returncode != 0 or (data is not None and data.get("is_error")):
        detail = (data or {}).get("result") or proc.stderr.strip() or f"종료 코드 {proc.returncode}"
        fail(f"claude -p 실패: {detail}")
    if data is None:
        fail(f"claude -p 출력 파싱 실패: {proc.stdout[:500]!r}")

    notes = str(data.get("result") or "").strip()
    if not notes:
        fail("claude -p가 빈 릴리스 노트를 반환했습니다.")
    return notes


@dataclass(frozen=True)
class ReleasePlan:
    """이번 실행이 릴리스를 새로 만들지(create), 에셋만 얹을지(append) 정한 결과."""

    mode: str  # "create" | "append"
    error: str | None = None


def plan_release(
    *,
    tag: str,
    prev_tag: str | None,
    existing_assets: list[str] | None,
    releases_json: str,
    force: bool,
    tag_sha: str | None,
    head_sha: str | None,
    newest_tag: str | None = None,
) -> ReleasePlan:
    """gh 조회 결과만 보고 이번 실행의 동작을 정한다(부수효과 없는 순수 함수).

    "버전 올리는 걸 잊었다" 가드를 **판정 근거 셋으로 늘린 것**이다. 플랫폼별로 따로 빌드하면
    두 번째 실행은 정상적으로도 "이전 릴리스 == 이번 태그" 상태가 되므로, 태그만 보고는 잊은
    것인지 두 번째 플랫폼인지 구분할 수 없다. 그래서 릴리스가 이미 있는 경로에서 아래 셋을
    모두 본다(--force는 전부 뚫는다):

    1. 그 릴리스에 내 채널의 releases.<channel>.json이 이미 있는가 → 이미 배포됨.
       파일 이름에 채널이 박혀 있어(win/osx) 플랫폼별로 정확히 한 번씩만 통과한다.
    2. 이 태그가 **최신** 릴리스인가. 낡은 체크아웃에서 돌리면 과거 태그에 에셋이 붙고 정작
       latest에는 그 OS 설치기가 영영 없는 상태가 된다.
    3. 태그가 가리키는 커밋 == 지금 HEAD인가. 1·2만으로는 **아직 한 번도 릴리스된 적 없는
       채널**에 가드가 하나도 걸리지 않는다 — 버전을 안 올린 채 HEAD에서 빌드하면 그 버전이
       아닌 코드가 그 버전으로 올라가고, 그 OS 사용자의 업데이트 피드가 그것을 새 버전으로
       알린다.

    ``tag_sha``/``head_sha``는 조회 실패 시 ``None``이며, 그때는 대조가 불가능하므로
    통과시키지 않고 중단한다(모르는 채 올리는 것이 이 가드가 막으려는 바로 그 사고다).
    """
    if existing_assets is None:
        # 릴리스 자체가 없다. prev_tag == tag는 정상적으로는 나올 수 없는 조합이지만,
        # gh 조회가 어긋났을 때 조용히 새 릴리스를 만들지 않도록 방어한다.
        if prev_tag == tag:
            return ReleasePlan(
                mode="create",
                error=(
                    f"버전이 이전 릴리스({tag})와 같습니다. "
                    "pyproject.toml의 [project].version을 올린 뒤 다시 실행하세요."
                ),
            )
        return ReleasePlan(mode="create")

    if releases_json in existing_assets and not force:
        return ReleasePlan(
            mode="append",
            error=(
                f"{tag} 릴리스에 이미 {releases_json}이 올라가 있습니다 — "
                "이 플랫폼 에셋은 이미 배포됐습니다. "
                "pyproject.toml의 버전을 올렸는지 확인하세요(덮어쓰려면 --force)."
            ),
        )

    if not force:
        # 낡은 체크아웃 방어. 정상적인 두 번째 플랫폼 실행은 첫 플랫폼이 방금 만든 릴리스를
        # 보므로 반드시 tag == newest다. **draft를 포함한** 최신 태그와 비교해야 한다 —
        # 배포가 draft로 만들어지기 때문이다.
        newest = newest_tag if newest_tag is not None else prev_tag
        if newest is not None and tag != newest:
            return ReleasePlan(
                mode="append",
                error=(
                    f"{tag}는 최신 릴리스({newest})가 아닙니다 — 오래된 커밋/버전에서 "
                    "실행 중입니다.\n"
                    "  git pull로 최신 커밋을 받고 pyproject.toml의 [project].version을 "
                    "확인한 뒤 다시 실행하세요(의도한 재업로드면 --force)."
                ),
            )
        if tag_sha is None or head_sha is None:
            return ReleasePlan(
                mode="append",
                error=(
                    f"{tag} 태그의 커밋을 확인하지 못했습니다(gh/git 조회 실패). 첫 번째 "
                    "플랫폼과 같은 커밋인지 대조할 수 없어 중단합니다(직접 확인했다면 --force)."
                ),
            )
        if tag_sha != head_sha:
            return ReleasePlan(
                mode="append",
                error=(
                    f"{tag} 태그의 커밋({tag_sha[:12]})과 지금 HEAD({head_sha[:12]})가 "
                    "다릅니다 — 이대로 올리면 그 버전이 아닌 코드가 그 버전으로 배포됩니다.\n"
                    "  * 버전을 올리는 걸 잊었다면: pyproject.toml의 [project].version을 "
                    "올리세요.\n"
                    "  * 두 번째 플랫폼이라면: 첫 번째와 같은 커밋에서 실행하세요 "
                    f"(git checkout {tag}).\n"
                    "  (의도한 것이면 --force)"
                ),
            )

    # 다른 플랫폼이 같은 커밋에서 방금 만들어 둔 릴리스다(또는 --force 로 재실행).
    return ReleasePlan(mode="append")


def collect_assets(out_dir: Path, target: str, version: str) -> list[Path]:
    """이번 플랫폼·이번 버전의 업로드 대상 파일(설치기 → full → delta → 피드 순).

    글롭은 scripts/build.py가 단일 소스로 정의한다. 여기서 다시 조립하면 build.py가 만든
    이름과 어긋나 "빌드는 됐는데 자동 업데이트가 안 되는" 조용한 실패가 난다.

    글롭에 **버전과 채널이 모두** 들어가 있어야 하는 이유가 둘 있다. ``vpk download github``가
    델타 계산 기준으로 이전 릴리스 nupkg를 같은 폴더에 받아 두고, 로컬에서 두 플랫폼을 다
    만들어 본 경우 다른 채널 파일도 섞인다. 확장자만 보고 고르면 그것들이 함께 올라간다.

    Portable.zip / assets.*.json / 레거시 RELEASES는 고르지 않는다 — 앱의 ``GithubSource``가
    쓰지 않으므로 릴리스를 무겁게 만들 뿐이다.

    Raises:
        ValueError: 필수 산출물(설치기, full nupkg, 피드)이 없을 때. delta는 첫 릴리스에
            없는 게 정상이라 없어도 통과한다.
    """
    installer_glob = build_script.setup_glob(target)
    installers = sorted(out_dir.glob(installer_glob))
    if not installers:
        raise ValueError(f"{out_dir}에서 {installer_glob}을 찾지 못했습니다(빌드 실패?).")

    full_glob = build_script.full_nupkg_glob(target, version)
    fulls = sorted(out_dir.glob(full_glob))
    if not fulls:
        raise ValueError(f"{out_dir}에서 {full_glob}을 찾지 못했습니다(빌드 실패?).")
    deltas = sorted(out_dir.glob(full_glob.replace("-full.nupkg", "-delta.nupkg")))

    feed = out_dir / build_script.releases_json_name(target)
    if not feed.is_file():
        raise ValueError(f"{feed}가 없습니다(빌드 실패?).")

    return [*installers, *fulls, *deltas, feed]


def create_release_command(
    tag: str, assets: list[Path], *, notes_path: Path, head_sha: str | None, publish: bool
) -> list[str]:
    """릴리스를 새로 만드는 ``gh`` 커맨드(첫 번째 플랫폼).

    ``--target``으로 태그를 **방금 빌드한 커밋**에 고정한다. 넘기지 않으면 gh는 원격 기본
    브랜치의 tip에 태그를 만드는데, 그 사이 다른 커밋이 올라와 있으면 태그가 배포된 산출물과
    다른 코드를 가리킨다(:func:`check_head_pushed` 참고).

    **기본은 draft다.** 한쪽 플랫폼만 올라간 상태로 공개하면 다른 OS 사용자는 받을 파일이
    없는 릴리스를 본다. 더 나쁜 것은 자동 업데이트다 — 그쪽 채널의 피드가 없는 릴리스가
    최신이 되면 기존 사용자가 업데이트를 못 받는다.
    """
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        *[str(path) for path in assets],
        "--title",
        tag,
        "--notes-file",
        str(notes_path),
    ]
    if head_sha:
        cmd += ["--target", head_sha]
    if not publish:
        cmd.append("--draft")
    return cmd


def append_assets_command(tag: str, assets: list[Path]) -> list[str]:
    """기존 릴리스에 이번 플랫폼 에셋만 얹는 ``gh`` 커맨드(두 번째 플랫폼).

    노트 관련 옵션을 **절대 넘기지 않는다** — 첫 플랫폼이 쓴 본문을 덮어쓰면 안 된다.
    ``--clobber``는 업로드가 중간에 끊겨 같은 이름 에셋이 남았을 때의 재실행 복구용이다.
    """
    return ["gh", "release", "upload", tag, *[str(path) for path in assets], "--clobber"]


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
        help="에셋 중복·최신 릴리스 아님·태그 커밋 불일치·미커밋·미push 검사를 모두 무시한다.",
    )
    args = parser.parse_args()

    require_gh()
    version = pyproject_version()
    tag = f"v{version}"
    target = build_script.current_target()
    channel = build_script.channel_for(target)

    # --dry-run은 빌드도 업로드도 하지 않으므로 아래 가드를 건너뛴다 — 무엇이 올라갈지만
    # 보려는 것뿐인데 커밋을 강요하면 쓸모가 없다.
    head_sha = head_commit()
    if not args.dry_run:
        lock_path = REPO_ROOT / "uv.lock"
        error = check_lockfile_version(
            lockfile_version(lock_path.read_text(encoding="utf-8"), project_name())
            if lock_path.is_file()
            else None,
            version,
            force=args.force,
        )
        if error:
            fail(error)
        error = check_worktree_clean(worktree_status(), force=args.force)
        if error:
            fail(error)
        error = check_head_pushed(
            head_sha,
            remote_has_head=remote_has_commit(head_sha) if head_sha else False,
            force=args.force,
        )
        if error:
            fail(error)

    existing = release_assets(tag)
    # 노트 기준점은 공개된 릴리스, 최신 여부 판정은 draft 포함(latest_release_tag 주석 참고).
    prev_tag = latest_release_tag()
    newest_tag = latest_release_tag(include_drafts=True)
    # 커밋 대조는 릴리스가 이미 있을 때만 의미가 있다(없으면 태그 자체가 아직 없다).
    tag_sha = tag_commit(tag) if existing is not None else None

    plan = plan_release(
        tag=tag,
        prev_tag=prev_tag,
        existing_assets=existing,
        releases_json=build_script.releases_json_name(target),
        force=args.force,
        tag_sha=tag_sha,
        head_sha=head_sha,
        newest_tag=newest_tag,
    )
    if plan.error:
        fail(plan.error)

    info(f"{tag} · {target}({channel} 채널) 배포")
    if plan.mode == "append":
        info(f"{tag} 릴리스가 이미 있습니다 — 노트는 그대로 두고 {channel} 에셋만 추가합니다.")

    # --dry-run은 "무엇이 올라갈지"만 보는 용도다. 그것 때문에 수 분짜리 빌드를 돌리지 않는다.
    if not args.skip_build and not args.dry_run:
        info("빌드 시작 (scripts/build.py)")
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "build.py")], cwd=REPO_ROOT
        )
        if result.returncode != 0:
            fail(f"빌드 실패(exit {result.returncode})")

    out_dir = build_script.velopack_output_dir()
    if not out_dir.is_dir():
        fail(f"Velopack 산출 폴더가 없습니다: {out_dir} (먼저 빌드하세요)")
    try:
        assets = collect_assets(out_dir, target, version)
    except ValueError as exc:
        fail(str(exc))
    info("업로드 대상:")
    for path in assets:
        info(f"  - {path.name}")

    if args.dry_run:
        info("--dry-run: 업로드하지 않고 종료합니다.")
        return 0

    notes_path = args.notes or (out_dir / build_script.RELEASE_NOTES)
    if plan.mode == "create":
        if not notes_path.is_file():
            # 파일이 없을 때만 초안을 만들어 **그 파일에 써 둔다**. 릴리스는 어차피 draft로
            # 만들어지므로 공개 전에 사람이 고칠 수 있다. 파일이 있으면 손대지 않는다 —
            # 사람이 쓴 노트를 자동 생성으로 덮어쓰는 일은 없어야 한다.
            info(f"{prev_tag or '(첫 릴리스)'} → {tag}")
            log = commit_log_since(prev_tag)
            if not log:
                fail(f"{prev_tag} 이후 커밋이 없습니다 — 릴리스할 변경사항이 없습니다.")
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            notes_path.write_text(generate_release_notes(prev_tag, tag, log), encoding="utf-8")
            info(f"릴리스 노트 초안 생성: {notes_path} (공개 전 검토하세요)")
        else:
            info(f"릴리스 노트: {notes_path}")
        info(f"GitHub 릴리스 생성/업로드: {tag}")
        check(
            create_release_command(
                tag, assets, notes_path=notes_path, head_sha=head_sha, publish=args.publish
            )
        )
    else:
        info(f"GitHub 릴리스에 {channel} 에셋 추가: {tag}")
        check(append_assets_command(tag, assets))

    if args.publish:
        # append 경로에서도 공개할 수 있어야 한다(두 번째 플랫폼이 마지막인 게 보통이다).
        info(f"릴리스 공개: {tag}")
        check(["gh", "release", "edit", tag, "--draft=false"])
        info(f"완료(공개): {build_script.REPO_URL}/releases/tag/{tag}")
    else:
        info(
            f"완료(draft): {build_script.REPO_URL}/releases/tag/{tag}\n"
            "  다른 플랫폼 산출물까지 올린 뒤 --publish로 공개하세요."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
