#!/usr/bin/env python3
"""배포 번들 코드 서명 — 환경변수가 없으면 미서명으로 진행한다.

이번 이관은 **미서명 배포**가 확정 전제다(핸드오프 §0-4). 그래서 이 모듈의 기본 동작은
"아무것도 하지 않고 빌드를 계속하게 두는 것"이고, 나중에 인증서를 갖추면 환경변수만 채워
서명 배포로 전환할 수 있게 자리만 뚫어 둔다.

플랫폼마다 서명 인자 체계가 완전히 다르다.

- **Windows**: vpk 가 파일마다 ``signtool sign <signParams> <file>``을 부르므로, signtool
  인자를 공백으로 이어 붙인 **문자열 하나**(``--signParams``)를 넘긴다.
- **macOS**: ``vpk pack``의 osx 경로에는 ``--signParams``가 아예 없다. 대신
  ``--signAppIdentity``/``--signInstallIdentity``/``--notaryProfile`` 같은 **개별 인자**를 넘긴다.

환경변수(전부 선택):
  NPC_SIGN_THUMBPRINT        [win] 인증서 저장소(CurrentUser\\My)의 인증서 지문(SHA1).
                             비밀번호가 명령줄에 노출되지 않아 개인 서명에 권장.
  NPC_SIGN_PFX               [win] 서명 인증서 .pfx 경로(위와 택일). CI 등 저장소를 못 쓰는 환경용.
  NPC_SIGN_PFX_PASSWORD      [win] .pfx 비밀번호(있으면).
  NPC_SIGN_TIMESTAMP_URL     [win] RFC3161 타임스탬프 서버(기본 digicert).
                             인증서 만료 후에도 서명이 유지된다.
  NPC_SIGN_APP_IDENTITY      [mac] 앱 코드 서명 식별자. ad-hoc 재서명은 ``-`` 하나면 된다.
  NPC_SIGN_INSTALL_IDENTITY  [mac] .pkg 설치기 서명 식별자.
  NPC_SIGN_NOTARY_PROFILE    [mac] notarytool에 저장해 둔 자격증명 프로파일 이름.

.. note::
    인자 조립 함수(:func:`velopack_sign_params_win`, :func:`velopack_sign_args_macos`)는
    호스트 OS를 보지 않는다. 어떤 인자 체계를 쓸지는 **빌드 타깃**이 정하는 것이지 빌드를
    돌리는 머신이 정하는 것이 아니다. 실제로 signtool을 실행하는 :func:`maybe_sign_bundle`만
    Windows를 요구한다.

사용:
  python scripts/sign.py <배포폴더|exe경로>   # 직접 서명(build.py 없이 재서명할 때)
  build.py가 빌드 직후 자동 호출(maybe_sign_bundle).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _common import REPO_ROOT, fail, info

# 서명 대상(번들 루트의 앱 실행 파일). Windows 전용 경로다 — macOS 번들은 .app 디렉터리라
# 이 이름으로 찾지 않고, Velopack이 pack 단계에서 직접 서명한다.
_APP_EXE = "naver-post-crawler.exe"
_DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"


def _env(name: str) -> str:
    """환경변수를 공백 제거해 읽는다(미설정이거나 공백뿐이면 빈 문자열)."""
    return os.environ.get(name, "").strip()


def find_signtool() -> Path | None:
    """Windows SDK(Windows Kits 10)에서 최신 x64 signtool.exe를 찾는다."""
    # Windows에서 os.environ 키는 대문자로 정규화된다(다른 OS에서는 어차피 없어 기본값을 쓴다).
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    base = Path(program_files_x86) / "Windows Kits" / "10" / "bin"
    if not base.exists():
        return None
    # bin/<sdk_ver>/x64/signtool.exe — 버전 내림차순으로 최신 우선.
    candidates = sorted(base.glob("*/x64/signtool.exe"), reverse=True)
    return candidates[0] if candidates else None


def _cert_args_win() -> list[str] | None:
    """환경변수에서 signtool 인증서 지정 인자를 만든다. 둘 다 없으면 None(=서명 스킵)."""
    thumbprint = _env("NPC_SIGN_THUMBPRINT")
    if thumbprint:
        # 저장소 인증서. 비밀번호가 명령줄에 노출되지 않는다.
        return ["/sha1", thumbprint]
    pfx = _env("NPC_SIGN_PFX")
    if pfx:
        args = ["/f", pfx]
        password = os.environ.get("NPC_SIGN_PFX_PASSWORD", "")
        if password:
            args += ["/p", password]
        return args
    return None


def velopack_sign_params_win() -> str | None:
    """Windows ``vpk pack --signParams``로 넘길 signtool 인자 문자열.

    인증서 미지정이면 None(=미서명). 비밀번호(``/p``)가 섞일 수 있으므로 이 문자열을 그대로
    로그에 남기지 말고 :func:`mask_sign_params`를 거친다.
    """
    cert_args = _cert_args_win()
    if cert_args is None:
        return None
    timestamp_url = _env("NPC_SIGN_TIMESTAMP_URL") or _DEFAULT_TIMESTAMP
    return " ".join([*cert_args, "/fd", "SHA256", "/tr", timestamp_url, "/td", "SHA256"])


def mask_sign_params(params: str) -> str:
    """서명 인자 문자열에서 .pfx 비밀번호(``/p <값>``)만 가린다.

    나머지 인자(인증서 경로·타임스탬프 URL)는 실패 원인을 짚는 데 필요하므로 그대로 둔다.
    """
    masked: list[str] = []
    hide_next = False
    for token in params.split(" "):
        if hide_next:
            masked.append("***")
            hide_next = False
            continue
        masked.append(token)
        hide_next = token == "/p"
    return " ".join(masked)


def velopack_sign_args_macos() -> list[str]:
    """macOS ``vpk pack``에 넘길 서명 인자 리스트. 전부 미설정이면 빈 리스트다.

    세 항목은 서로 독립이다. ad-hoc 재서명(D-3)처럼 앱 식별자만 ``-``로 주는 경우도 그대로
    성립해야 하므로, 설정된 항목만 담아 돌려준다.
    """
    args: list[str] = []
    for flag, name in (
        ("--signAppIdentity", "NPC_SIGN_APP_IDENTITY"),
        ("--signInstallIdentity", "NPC_SIGN_INSTALL_IDENTITY"),
        ("--notaryProfile", "NPC_SIGN_NOTARY_PROFILE"),
    ):
        value = _env(name)
        if value:
            args += [flag, value]
    return args


def sign_file(signtool: Path, target: Path, cert_args: list[str]) -> None:
    """signtool로 파일 하나를 SHA256 + RFC3161 타임스탬프로 서명한다(실패 시 종료)."""
    timestamp_url = _env("NPC_SIGN_TIMESTAMP_URL") or _DEFAULT_TIMESTAMP
    cmd = [
        str(signtool),
        "sign",
        *cert_args,
        "/fd",
        "SHA256",
        "/tr",
        timestamp_url,
        "/td",
        "SHA256",
        str(target),
    ]
    info(f"서명: {target.name}")
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        out = (result.stdout or "") + (result.stderr or "")
        # 비밀번호(/p)는 로그에 남기지 않는다.
        fail(
            f"서명 실패(exit {result.returncode}): {mask_sign_params(' '.join(cmd))}\n{out.strip()}"
        )


def maybe_sign_bundle(dst: Path) -> bool:
    """Windows 배포 폴더의 앱 exe를 서명한다. 인증서 미지정이면 건너뛴다.

    Returns:
        서명했으면 True, 건너뛰었으면 False.

    건너뛸 때는 이유를 반드시 로그로 남긴다. 조용히 False를 돌려주면 서명된 줄 알고 배포하게 된다.
    """
    if sys.platform != "win32":
        info(
            f"코드 서명 건너뜀 ({sys.platform} — signtool은 Windows 전용입니다. "
            "macOS는 vpk가 pack 단계에서 서명하며, 미설정이면 미서명 배포입니다)."
        )
        return False
    cert_args = _cert_args_win()
    if cert_args is None:
        info("코드 서명 건너뜀 (NPC_SIGN_THUMBPRINT/NPC_SIGN_PFX 미설정 → 미서명 배포).")
        return False
    signtool = find_signtool()
    if signtool is None:
        fail("signtool.exe를 찾지 못했습니다. Windows SDK(서명 도구)를 설치하세요.")
    exe = dst / _APP_EXE
    if not exe.exists():
        fail(f"서명 대상 앱 실행 파일이 없습니다: {exe}")
    sign_file(signtool, exe, cert_args)
    info(f"서명 완료: {exe}")
    return True


def _main(argv: list[str]) -> int:
    # 직접 실행 경로는 여기서 끊는다. macOS에서 조용히 아무것도 안 하고 0으로 끝나면
    # 서명된 줄 알고 배포하게 된다.
    if sys.platform != "win32":
        fail(
            f"직접 서명은 Windows 전용입니다(현재 {sys.platform}). macOS 서명은 vpk pack이 "
            "NPC_SIGN_APP_IDENTITY 계열 환경변수를 받아 처리합니다."
        )
    if len(argv) != 1:
        fail("사용법: python scripts/sign.py <배포폴더|exe경로>")
    target = Path(argv[0]).resolve()
    if target.is_dir():
        maybe_sign_bundle(target)
    elif target.is_file():
        cert_args = _cert_args_win()
        if cert_args is None:
            fail("서명할 인증서를 지정하세요(NPC_SIGN_THUMBPRINT 또는 NPC_SIGN_PFX).")
        signtool = find_signtool()
        if signtool is None:
            fail("signtool.exe를 찾지 못했습니다. Windows SDK를 설치하세요.")
        sign_file(signtool, target, cert_args)
    else:
        fail(f"경로를 찾을 수 없습니다: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
