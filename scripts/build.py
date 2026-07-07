#!/usr/bin/env python3
"""flet pack(PyInstaller)으로 단일 실행파일을 만들고 배포용 zip으로 압축한다.

사용:
    python scripts/build.py

결과물:
  - dist/naver-post-crawler(.exe): 단일 실행파일(Windows .exe / Linux 바이너리 / macOS .app).
  - dist/naver-post-crawler-<target>.zip: 업데이터(naver_post_crawler.updater)와 GitHub
    Releases가 소비하는 릴리스 에셋. win/linux는 naver-post-crawler/ 폴더 안에 실행파일을
    담아 압축하고(풀면 naver-post-crawler/<exe> 구조), macOS는 .app 번들을 그대로 압축한다.

Flutter/네이티브 툴체인은 필요 없다(PyInstaller가 Python 런타임을 그대로 묶는다). 필요한
의존성(pyinstaller, flet-cli)은 dev 그룹에 있어 'uv sync'가 준비한다.

앱 데이터 저장 위치: naver_post_crawler.cookie.app_data_dir()이 실행 파일 옆 storage/
에 저장한다(PyInstaller frozen 감지). 폴더째 옮겨도 데이터가 따라오는 포터블 동작이다.

(개발 중 빠른 실행은 'python scripts/run.py --gui')
"""

from __future__ import annotations

import os
import platform
import shutil
import zipfile
from pathlib import Path

from _common import REPO_ROOT, check, fail, info, require_uv

_PRODUCT = "Naver Blog Backup"

# flet pack(PyInstaller) 단일 실행파일 빌드 설정.
# 진입점은 src/main.py다. pack은 이 스크립트의 디렉터리(src/)를 import 경로에 올리므로
# naver_post_crawler 패키지를 그대로 찾을 수 있다.
_PACK_ENTRY = REPO_ROOT / "src" / "main.py"
_PACK_NAME = "naver-post-crawler"  # 생성될 실행파일 이름
_PACK_DIST = REPO_ROOT / "dist"  # 단일 실행파일이 놓일 디렉터리
# flet pack은 cwd/build 디렉터리를 통째로 지운다. 저장소 루트에서 바로 돌리면 다른
# build/ 산출물과 충돌할 수 있어 pack은 전용 작업 디렉터리에서 돌린다.
_PACK_WORK = REPO_ROOT / ".pack-build"


def _pack_artifact_path() -> Path:
    """현재 OS에서 flet pack이 만드는 단일 실행파일/번들의 경로를 돌려준다."""
    system = platform.system()
    if system == "Windows":
        return _PACK_DIST / f"{_PACK_NAME}.exe"
    if system == "Darwin":
        return _PACK_DIST / f"{_PACK_NAME}.app"  # macOS는 .app 번들
    return _PACK_DIST / _PACK_NAME


def _clean_prior_artifact() -> None:
    """이전 단일 실행파일을 미리 지운다. 잠겨 있으면 원인을 짚어 명확히 안내한다.

    flet pack은 dist를 rmtree(ignore_errors=True)로 지우는데, 결과물이 잠겨 있으면
    (앱이 실행 중이거나 백신이 검사 중) 조용히 남겨두고 PyInstaller의 EXE 단계가
    os.remove에서 PermissionError로 죽는다 — 불친절한 트레이스백이 그대로 노출된다.
    여기서 먼저 지워 보고, 실패하면 사용자가 조치할 수 있게 원인을 안내한다.
    """
    artifact = _pack_artifact_path()
    if not artifact.exists():
        return
    try:
        if artifact.is_dir():  # macOS .app 번들
            shutil.rmtree(artifact)
        else:
            artifact.unlink()
    except OSError:
        fail(
            f"기존 결과물을 지울 수 없습니다(잠김): {artifact}\n"
            "  실행 중인 앱을 모두 닫은 뒤 다시 시도하세요.\n"
            "  (백신 실시간 검사가 파일을 잡고 있을 수도 있습니다.)"
        )


def verify_pack_artifact() -> None:
    """flet pack(PyInstaller) 결과물(단일 실행파일/번들)이 생겼는지 확인한다."""
    if not _PACK_DIST.exists():
        fail(f"빌드가 끝났지만 {_PACK_DIST} 가 없습니다.")
    artifact = _pack_artifact_path()
    if not artifact.exists():
        fail(f"빌드가 끝났지만 결과물을 찾지 못했습니다: {artifact}")
    info(f"완료: {artifact}")


def pack_app(build_env: dict[str, str]) -> None:
    """`flet pack`으로 단일 실행파일을 만든다."""
    # 이전 결과물이 잠겨 있으면(앱 실행 중 등) 먼저 명확히 안내하고 멈춘다.
    # 그러지 않으면 flet pack이 마지막 단계에서 PermissionError 트레이스백으로 죽는다.
    _clean_prior_artifact()

    # flet pack은 cwd/build를 통째로 지우므로 전용 작업 디렉터리 안에서 실행한다.
    if _PACK_WORK.exists():
        shutil.rmtree(_PACK_WORK)
    _PACK_WORK.mkdir(parents=True)

    info("flet pack (단일 실행파일)")
    check(
        ["uv", "run", "flet", "pack", str(_PACK_ENTRY),
         "--name", _PACK_NAME, "--product-name", _PRODUCT,
         "--distpath", str(_PACK_DIST), "--yes"],
        env=build_env,
        cwd=_PACK_WORK,
    )

    # PyInstaller 중간 산출물(작업 디렉터리)은 결과물이 아니므로 정리한다.
    shutil.rmtree(_PACK_WORK, ignore_errors=True)
    verify_pack_artifact()


def _current_pack_target() -> str:
    """현재 OS의 릴리스 타깃 이름(windows/macos/linux). _pack_artifact_path와 같은 매핑."""
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system(), "windows"
    )


def compress_pack_artifact() -> None:
    """flet pack 결과물을 릴리스 에셋 zip(dist/naver-post-crawler-<target>.zip)으로 만든다.

    업데이터(naver_post_crawler.updater)가 소비하는 에셋이다. updater.extract() 계약에
    맞춰, 단일 파일(win .exe/linux 바이너리)은 naver-post-crawler/ 폴더 안에 담아 압축
    하고, macOS .app 번들은 최상위 폴더 이름을 보존해 단일 디렉터리로 풀리게 한다.
    zip을 풀면 naver-post-crawler/<exe> 구조가 되어, exe 실행 시 생성되는 storage/ 등
    부산물이 같은 폴더 안에 모인다.
    """
    target = _current_pack_target()
    artifact = _pack_artifact_path()
    zip_path = _PACK_DIST / f"{_PACK_NAME}-{target}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if artifact.is_dir():  # macOS .app 번들: 최상위 폴더 이름을 보존해 통째로 압축.
            for path in sorted(artifact.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(artifact.parent)))
        else:  # win .exe / linux 바이너리: naver-post-crawler/ 폴더 안에 담아 압축한다.
            # 압축을 풀면 naver-post-crawler/<exe> 구조가 되어, exe 실행 시 생성되는
            # storage/ 등의 부산물이 같은 폴더 안에 모인다.
            zf.write(artifact, arcname=f"{_PACK_NAME}/{artifact.name}")
    info(f"릴리스 에셋: {zip_path}")


def main() -> int:
    require_uv()

    # flet pack의 진행 표시(rich)는 체크마크 등 이모지를 stdout에 쓰는데, 한국어
    # Windows 콘솔 기본 코덱(cp949)으로는 인코딩할 수 없어 UnicodeEncodeError로
    # 빌드가 죽는다. 자식 Python을 UTF-8 모드로 강제해 회피한다(다른 OS에선 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])

    pack_app(build_env)
    compress_pack_artifact()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
