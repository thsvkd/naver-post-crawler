"""GitHub Releases 기반 자체 업데이트(커스텀 사이드카 패턴, 단일 exe 대상).

흐름: 최신 릴리스 감지 → 에셋(zip) 다운로드 → SHA256 검증(GitHub digest 대조) →
staging 에 압축 해제 → 사이드카 스크립트로 실행 중 exe 교체 후 재실행.

Windows 에서 실행 중인 exe 는 잠기므로 in-place 덮어쓰기가 안 된다. 그래서 번들 밖
(temp)의 사이드카 프로세스가 "앱 종료 대기 → 파일 rename 스왑 → 재실행 → 롤백"을 수행한다.

이 프로젝트는 ``flet pack``(PyInstaller) 단일 exe 로 배포하므로(scripts/build.py --onefile),
설치 위치는 실행 중인 exe 파일 자체(:func:`install_exe`)이고, 교체 대상도 폴더가 아니라
그 exe 파일 하나다. 개발 실행(비-frozen)에서는 :func:`is_packaged` 가드로 자기 교체를 막는다.

주의(배포 전제): 릴리스는 origin 저장소(``thsvkd/naver-blog-crawler``)에 태그 ``v<version>``
으로 올리며, 에셋 이름은 :func:`asset_name` 규칙(``naver-post-crawler-<target>.zip``)을 따른다.
순수 로직(버전 비교, 에셋 선택, SHA256, 응답 파싱, 사이드카 생성)은 단위 테스트로 검증하고,
실제 교체·재실행 전체 경로는 Windows 에서 수동 E2E 로 확인한다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# 릴리스가 사는 GitHub 저장소(origin). 패키지명(naver-post-crawler)과 다름에 주의.
REPO_OWNER = "thsvkd"
REPO_NAME = "naver-blog-crawler"

_API_LATEST = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
# GitHub 은 User-Agent 없는 요청을 403 으로 거부한다.
_USER_AGENT = "naver-post-crawler-updater"

# 교체 후 재실행할 실행 파일 이름. scripts/build.py 의 _PACK_NAME 과 일치해야 한다.
APP_EXE_NAME = "naver-post-crawler.exe" if sys.platform == "win32" else "naver-post-crawler"

# staging(압축 해제) 디렉터리 이름. install exe 와 같은 볼륨에 두어야 rename 이 원자적이다.
_STAGING_DIRNAME = ".naver_update_staging"

# 에셋 다운로드 크기 상한(500MiB). 악의적/오동작 서버가 무한정 스트리밍하는 것을 막는다.
MAX_ASSET_BYTES = 500 * 1024 * 1024


@dataclass
class Release:
    """업데이트 대상 릴리스."""

    tag: str
    version: str
    asset_name: str
    asset_url: str
    sha256: str | None  # GitHub 이 노출하는 에셋 digest (없을 수도 있음)
    notes: str


# -- 버전 / 에셋 이름 --------------------------------------------------------
def parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' → (1, 2, 3). 비교용 정수 튜플.

    prerelease/build 접미사(-rc1, +build)는 무시하고 숫자 코어만 본다. 그렇지 않으면
    '1.2.3-rc1' → (1,2,3,1) 이 되어 최종 '1.2.3' → (1,2,3) 보다 크게 정렬되는 버그가 난다.
    """
    # 선행 v/V 를 떼고, 첫 -/+ 앞의 숫자 코어만 취한다(prerelease/build 접미사 무시).
    core = re.split(r"[-+]", (tag or "").lstrip("vV"), maxsplit=1)[0]
    nums = re.findall(r"\d+", core)
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(candidate_tag: str, current_version: str) -> bool:
    """candidate_tag 가 current_version 보다 높은 버전인지."""
    return parse_version(candidate_tag) > parse_version(current_version)


def current_target() -> str:
    """현재 OS 의 배포 타깃 이름(windows/macos/linux)."""
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system(), "windows"
    )


def asset_name(target: str) -> str:
    """릴리스 에셋 파일명 규칙(build.py 산출물과 동일). 변형(cpu/gpu)은 없다."""
    return f"naver-post-crawler-{target}.zip"


def install_exe() -> Path:
    """설치된 실행 파일 경로. PyInstaller onefile 에서 sys.executable 은 실제 exe 다."""
    return Path(sys.executable).resolve()


def is_packaged() -> bool:
    """패키징된(frozen) 실행인지. 개발 실행에서 자기 교체를 막는 가드에 쓴다."""
    return getattr(sys, "frozen", False)


# -- 릴리스 조회 / 다운로드 --------------------------------------------------
def parse_latest(data: dict, current_version: str, target: str) -> Release | None:
    """GitHub /releases/latest 응답을 파싱해 업데이트가 있으면 Release 를 돌려준다.

    더 높은 버전이 아니거나 이 타깃에 맞는 에셋이 없으면 None. digest 는 "sha256:..."
    형식이며 접두사를 떼어 :attr:`Release.sha256` 로 담는다(없으면 None).
    """
    tag = data.get("tag_name") or ""
    if not is_newer(tag, current_version):
        return None
    want = asset_name(target)
    for asset in data.get("assets", []):
        if asset.get("name") == want:
            url = asset.get("browser_download_url")
            if not url:
                # 이름은 맞지만 다운로드 URL이 없는 손상된 항목이다. KeyError로 죽는 대신
                # 이 항목만 건너뛴다(같은 이름의 다른 정상 항목이 있을 수도 있으므로).
                continue
            digest = asset.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return Release(
                tag=tag,
                version=tag.lstrip("vV"),
                asset_name=want,
                asset_url=url,
                sha256=sha,
                notes=data.get("body") or "",
            )
    return None


def check_latest(
    current_version: str,
    target: str,
    *,
    owner: str = REPO_OWNER,
    repo: str = REPO_NAME,
    timeout: int = 10,
    client: httpx.Client | None = None,
) -> Release | None:
    """GitHub 에서 최신 릴리스를 조회한다(httpx). 네트워크 오류는 호출자가 처리한다.

    client 를 주면 그걸 재사용한다(테스트에서 httpx.MockTransport 주입용). 미지정 시
    내부에서 만들어 쓰고 닫는다. 릴리스가 하나도 없는 저장소는 404 를 주는데, 이는
    오류가 아니라 "업데이트 없음"이므로 None 으로 처리한다(그 밖의 상태 코드·네트워크
    오류는 그대로 전파해 호출자가 실패로 알린다).
    """
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(_API_LATEST.format(owner=owner, repo=repo), headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return parse_latest(data, current_version, target)
    finally:
        if owns_client:
            client.close()


def sha256_of(path: str | Path) -> str:
    """파일의 SHA256 hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    release: Release,
    dest_dir: str | Path,
    *,
    progress_cb=None,
    timeout: int = 120,
    client: httpx.Client | None = None,
) -> Path:
    """에셋 zip 을 dest_dir 에 스트리밍 저장하고 SHA256 을 검증한다.

    https 가 아니면 요청 전에 거부한다. digest 가 있으면 불일치 시 파일을 지우고
    RuntimeError. progress_cb(0.0~1.0) 로 진행률 보고. client 를 주면 재사용한다
    (테스트에서 httpx.MockTransport 주입용).
    """
    # https 검증은 클라이언트를 만지기 전에 한다 — non-https URL 이면 요청 자체를 보내지 않는다.
    if not release.asset_url.lower().startswith("https://"):
        raise RuntimeError(f"에셋 URL 이 https 가 아닙니다: {release.asset_url}")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / release.asset_name

    headers = {"User-Agent": _USER_AGENT}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        with client.stream("GET", release.asset_url, headers=headers) as resp:
            resp.raise_for_status()
            # 리다이렉트를 따라간 최종 URL도 https 여야 한다. 그러지 않으면 서버가
            # https→http 로 다운그레이드시켜 전송 중 변조 여지를 만들 수 있다.
            if not str(resp.url).lower().startswith("https://"):
                raise RuntimeError("리다이렉트 대상이 https 가 아닙니다")
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            try:
                with open(out, "wb") as fh:
                    for chunk in resp.iter_bytes(256 * 1024):
                        fh.write(chunk)
                        done += len(chunk)
                        if done > MAX_ASSET_BYTES:
                            raise RuntimeError("에셋 크기가 허용 한도를 초과했습니다")
                        if progress_cb and total:
                            progress_cb(done / total)
            except RuntimeError:
                out.unlink(missing_ok=True)
                raise
        if release.sha256:
            actual = sha256_of(out)
            if actual.lower() != release.sha256.lower():
                out.unlink(missing_ok=True)
                raise RuntimeError(
                    f"다운로드 파일의 SHA256 이 일치하지 않습니다: {release.asset_name}"
                )
        else:
            # digest 가 없으면 무결성은 TLS(https)만으로 보증된다.
            logger.warning(
                "릴리스 에셋에 digest 가 없어 SHA256 검증을 건너뜁니다(TLS 만으로 신뢰): %s",
                release.asset_name,
            )
        return out
    finally:
        if owns_client:
            client.close()


def extract(
    zip_path: str | Path, staging_dir: str | Path, *, expected_name: str | None = None
) -> Path:
    """zip 을 staging_dir 아래에 풀고, 새 실행 파일 경로를 돌려준다.

    최상위 엔트리가 정확히 하나여야 한다. 그 엔트리가 파일이면 직접 반환하고,
    폴더이면 그 안의 파일을 정확히 하나 찾아 반환한다(naver-post-crawler/<exe> 폴더
    배포 zip 지원). 어느 경우든 다중 엔트리나 중첩 폴더는 거부한다. expected_name
    을 주면 파일 이름이 다를 때 그 이름으로 rename 해 반환한다 — 사이드카에 들어갈
    파일명을 에셋 zip 내부 이름(신뢰 불가)이 아닌 고정값으로 못박아, zip 안의 이름
    조작으로 임의 경로를 만드는 것을 원천 차단한다.
    """
    staging_dir = Path(staging_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging_dir)
    entries = list(staging_dir.iterdir())
    if len(entries) != 1:
        raise RuntimeError("예상과 다른 에셋 구성(엔트리 수)")
    entry = entries[0]
    if entry.is_dir():
        # 폴더 배포 zip(naver-post-crawler/<exe>): 폴더 안에서 실행 파일을 찾는다.
        inner = [p for p in entry.iterdir() if p.is_file()]
        if len(inner) != 1:
            raise RuntimeError("에셋 폴더 안에 실행 파일이 정확히 하나여야 합니다")
        entry = inner[0]
    if not entry.is_file():
        raise RuntimeError("에셋 엔트리가 파일이 아닙니다")
    if expected_name is not None and entry.name != expected_name:
        target = entry.parent / expected_name
        entry.rename(target)
        entry = target
    return entry


def staging_dir(install_path: str | Path) -> Path:
    """install exe 와 같은 볼륨(폴더)에 두는 staging(압축 해제) 디렉터리 경로.

    rename 이 원자적이려면 staging 도 install 과 같은 볼륨이어야 하므로, 항상 이
    헬퍼로 계산해 호출자가 :data:`_STAGING_DIRNAME` 을 직접 참조하지 않게 한다.
    """
    return Path(install_path).parent / _STAGING_DIRNAME


# -- 사이드카 교체 스크립트 --------------------------------------------------
# 앱(PID) 종료 대기 → install→backup rename → new→install rename → 재실행 →
# 백업·자기 삭제. rename 실패 시 부분 파일을 지우고 백업을 되돌린다(단일 파일 스왑).
_WIN_SIDECAR = """@echo off
setlocal
:waitloop
tasklist /fi "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto waitloop
)
del "{backup}" 2>nul
move /Y "{install}" "{backup}" >nul || goto fail
move /Y "{new}" "{install}" >nul || goto restore
start "" "{install}"
del "{backup}" 2>nul
del "%~f0"
exit /b 0
:restore
del "{install}" 2>nul
move /Y "{backup}" "{install}" >nul
:fail
start "" "{install}"
del "%~f0"
exit /b 1
"""

# 첫 mv(install→backup)가 성공했을 때만 롤백(install 삭제 + backup 복원)한다. 이전 버전은
# 바깥쪽 무조건 rm -f "{install}" 때문에, 첫 mv 자체가 실패해도(즉 install이 그대로 원래
# 자리에 있는데도) install을 지워버려 백업도 새 파일도 없는 상태로 데이터 손실이 날 수 있었다.
_POSIX_SIDECAR = """#!/bin/sh
while kill -0 {pid} 2>/dev/null; do sleep 1; done
if mv "{install}" "{backup}"; then
  if mv "{new}" "{install}"; then
    rm -f "{backup}"
  else
    rm -f "{install}"
    mv "{backup}" "{install}"
  fi
fi
"{install}" &
rm -- "$0"
"""


def write_sidecar(
    temp_dir: str | Path,
    *,
    pid: int,
    install_exe: str | Path,
    new_exe: str | Path,
    app_exe: str,
) -> Path:
    """번들 밖(temp)에 단일 파일 교체 스크립트를 쓴다(exe 옆에 두면 그것도 잠긴다).

    앱(PID) 종료 대기 → install exe 를 backup 으로 rename → new exe 를 install 로 rename →
    재실행 → 백업·자기 삭제. 실패 시 부분 파일을 지우고 백업을 되돌린다.
    """
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    install_exe = Path(install_exe)
    # 단일 파일 스왑: 설치 exe 옆에 <stem>_backup<suffix> 로 백업 파일을 둔다.
    backup = install_exe.parent / (install_exe.stem + "_backup" + install_exe.suffix)

    if sys.platform == "win32":
        script = _WIN_SIDECAR.format(
            pid=pid,
            install=str(install_exe),
            backup=str(backup),
            new=str(new_exe),
        )
        path = temp_dir / "naver_update.bat"
    else:
        script = _POSIX_SIDECAR.format(
            pid=pid,
            install=str(install_exe),
            backup=str(backup),
            new=str(new_exe),
        )
        path = temp_dir / "naver_update.sh"

    path.write_text(script, encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o755)
    return path


def apply_and_restart(
    new_exe: str | Path,
    *,
    app_exe: str = APP_EXE_NAME,
    install_path: Path | None = None,
) -> None:
    """사이드카를 temp 에 만들어 detached 로 실행하고 현재 프로세스를 종료한다.

    install_path 미지정 시 :func:`install_exe` 를 쓴다. 호출 즉시 앱이 종료되므로
    호출 전 사용자에게 재시작 안내를 마쳐야 한다.

    Windows 전용이다. macOS/Linux 사이드카·롤백 경로는 아직 배포 대상이 아니므로(단일
    exe 배포는 Windows 만 검증됨) 다른 플랫폼에서는 자기 교체를 시도하지 않고 거부한다.
    """
    if sys.platform != "win32":
        raise RuntimeError("자동 업데이트 적용은 현재 Windows 에서만 지원됩니다.")
    target_exe = Path(install_path) if install_path else install_exe()
    # 매번 새 무작위 이름의 temp 디렉터리를 써서, 고정 경로를 노리는 TOCTOU/충돌 공격
    # 표면을 줄인다.
    temp_dir = Path(tempfile.mkdtemp(prefix="naver_update_"))
    script = write_sidecar(
        temp_dir,
        pid=os.getpid(),
        install_exe=target_exe,
        new_exe=new_exe,
        app_exe=app_exe,
    )
    # 사이드카를 현재 프로세스와 분리(detached)해 띄운다. 앱이 종료돼도 계속 돌아야 한다.
    # CREATE_NO_WINDOW(0x08000000): 콘솔 창을 띄우지 않는다.
    # CREATE_NEW_PROCESS_GROUP(0x00000200): 현재 프로세스와 별도 프로세스 그룹으로 만들어
    # 부모(현재 프로세스) 종료 시그널/콘솔 이벤트에 함께 묶이지 않게 한다.
    subprocess.Popen(
        ["cmd", "/c", str(script)],
        creationflags=0x08000000 | 0x00000200,
        close_fds=True,
    )
    os._exit(0)
