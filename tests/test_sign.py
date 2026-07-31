"""코드 서명 인자 조립(scripts/sign.py) 검증.

확정 전제 4에 따라 이번 이관은 **미서명 배포**다. 다만 나중에 환경변수만 채우면 서명으로
전환할 수 있게 자리를 뚫어 둔다. 그래서 두 가지가 계약이다.

1. 서명 환경변수가 비어 있으면 서명 인자를 만들지 않고(None/빈 리스트) 빌드가 미서명으로
   그대로 진행된다. vpk는 ``--signAppIdentity``가 비면 경고만 찍고 통과한다.
2. 환경변수가 있으면 **플랫폼별로 다른 인자 체계**로 조립된다. Windows는 signtool 인자를
   담은 ``--signParams`` 문자열 하나이고, macOS는 ``--signAppIdentity`` 계열 인자 리스트다
   (``vpk osx pack``에는 ``--signParams``가 아예 없다).

Windows 경로에는 .pfx 비밀번호(``/p``)가 섞일 수 있으므로 로그로 나가는 표현은 마스킹된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sign

# sign.py가 읽는 환경변수 전부. 테스트마다 전부 지워 '미설정' 상태를 결정적으로 만든다.
_WINDOWS_ENV = (
    "NPC_SIGN_THUMBPRINT",
    "NPC_SIGN_PFX",
    "NPC_SIGN_PFX_PASSWORD",
    "NPC_SIGN_TIMESTAMP_URL",
)
_MACOS_ENV = (
    "NPC_SIGN_APP_IDENTITY",
    "NPC_SIGN_INSTALL_IDENTITY",
    "NPC_SIGN_NOTARY_PROFILE",
)


@pytest.fixture(autouse=True)
def _clear_sign_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """개발 머신에 남아 있는 서명 환경변수가 테스트에 새지 않게 한다."""
    for name in (*_WINDOWS_ENV, *_MACOS_ENV):
        monkeypatch.delenv(name, raising=False)


# -- 미설정 = 서명 스킵 -----------------------------------------------------------------


def test_windows_sign_params_is_none_when_unset() -> None:
    # covers: Test-14
    assert sign.velopack_sign_params_win() is None


def test_macos_sign_args_is_empty_when_unset() -> None:
    # covers: Test-14
    assert sign.velopack_sign_args_macos() == []


def test_maybe_sign_bundle_skips_without_certificate(tmp_path: Path) -> None:
    # covers: Test-14 (인증서 미지정이면 실패가 아니라 '건너뜀' — 빌드는 계속된다)
    assert sign.maybe_sign_bundle(tmp_path) is False


# -- Windows: signtool 인자를 담은 --signParams 문자열 ------------------------------------


def test_windows_sign_params_from_thumbprint(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-15
    monkeypatch.setenv("NPC_SIGN_THUMBPRINT", "ABCD1234")

    params = sign.velopack_sign_params_win()

    assert params is not None
    assert "/sha1 ABCD1234" in params
    assert "/fd SHA256" in params
    assert "/td SHA256" in params
    assert "/tr " in params  # RFC3161 타임스탬프(기본값이라도 반드시 붙는다)


def test_windows_sign_params_uses_custom_timestamp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-15
    monkeypatch.setenv("NPC_SIGN_THUMBPRINT", "ABCD1234")
    monkeypatch.setenv("NPC_SIGN_TIMESTAMP_URL", "http://ts.example.test")

    params = sign.velopack_sign_params_win()

    assert params is not None
    assert "/tr http://ts.example.test" in params


def test_windows_sign_params_masks_pfx_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-15
    monkeypatch.setenv("NPC_SIGN_PFX", r"C:\certs\app.pfx")
    monkeypatch.setenv("NPC_SIGN_PFX_PASSWORD", "s3cret-passphrase")

    params = sign.velopack_sign_params_win()

    assert params is not None
    assert r"/f C:\certs\app.pfx" in params
    assert "/p s3cret-passphrase" in params

    masked = sign.mask_sign_params(params)

    assert "s3cret-passphrase" not in masked, "비밀번호가 로그로 새면 안 된다"
    assert "/p " in masked
    assert r"/f C:\certs\app.pfx" in masked  # 마스킹은 비밀번호에만 적용된다


# -- macOS: --signAppIdentity 계열 인자 리스트(--signParams가 아니다) ---------------------


def test_macos_sign_args_builds_identity_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-15
    monkeypatch.setenv("NPC_SIGN_APP_IDENTITY", "Developer ID Application: thsvkd")
    monkeypatch.setenv("NPC_SIGN_INSTALL_IDENTITY", "Developer ID Installer: thsvkd")
    monkeypatch.setenv("NPC_SIGN_NOTARY_PROFILE", "npc-notary")

    args = sign.velopack_sign_args_macos()

    assert args == [
        "--signAppIdentity",
        "Developer ID Application: thsvkd",
        "--signInstallIdentity",
        "Developer ID Installer: thsvkd",
        "--notaryProfile",
        "npc-notary",
    ]
    assert "--signParams" not in args, "vpk osx pack에는 --signParams가 없다"


def test_macos_sign_args_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-15 (ad-hoc 재서명처럼 앱 식별자만 주는 경우도 성립해야 한다 — D-3)
    monkeypatch.setenv("NPC_SIGN_APP_IDENTITY", "-")

    args = sign.velopack_sign_args_macos()

    assert args == ["--signAppIdentity", "-"]
