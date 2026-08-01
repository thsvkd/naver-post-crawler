"""세션 자격증명을 OS 보관소에 보관한다 — macOS 키체인 / Windows 자격 증명 관리자.

저장하는 값은 네이버 로그인 세션 그 자체다. 평문 파일로 두면 앱을 지워도 디스크에 남고,
제거와 무관하게 평소에도 노출된 상태가 된다. OS 보관소에 넣으면 (1) 평문이 사라지고,
(2) 남더라도 암호화돼 있으며, (3) 사용자가 "키체인 접근"·"자격 증명 관리자"에서 직접
확인하고 지울 수 있다.

.. important::
    백엔드는 **명시적으로 지정한다**. ``keyring.get_keyring()``의 엔트리포인트 자동 탐색은
    패키지 메타데이터(dist-info)를 읽는데, ``flet build`` 번들에는 그게 없을 수 있다.
    그러면 탐색이 조용히 빈 백엔드를 골라 **개발 환경에서는 통과하고 배포본에서만**
    저장이 안 되는 상태가 된다.
"""

from __future__ import annotations

import logging
import sys

from .errors import CredentialStoreError

logger = logging.getLogger(__name__)

# 보관소 항목을 식별하는 이름. Windows 자격 증명 관리자에서는 이 조합이 대상 이름이 되고,
# 네이티브 제거 훅이 같은 이름으로 항목을 지운다(scripts/flet_template.py).
SERVICE = "naver-post-crawler"
ACCOUNT = "cafe-cookie"

# Windows 자격 증명 관리자의 blob 한도(CRED_MAX_CREDENTIAL_BLOB_SIZE). UTF-16으로
# 저장되므로 실질 한도는 이 값의 절반 글자 수다. 넘으면 CredWrite가 실패하는데, 그대로
# 두면 "로그인은 했는데 저장이 안 되는" 증상이 되므로 저장 전에 막는다.
#
# macOS 키체인에는 이런 제약이 없지만 **양 플랫폼에 같은 한도를 적용한다**. 같은 계정이
# 한쪽에서만 저장되는 상황을 만들지 않고, 한도 초과를 개발 중에 드러내기 위해서다.
MAX_SECRET_BYTES = 2560

# keyring의 Windows 백엔드가 실제로 만드는 대상 이름. 기본 대상은 서비스 이름이고,
# 같은 서비스에 다른 계정이 있으면 ``계정@서비스`` 복합 이름도 함께 쓴다.
# 제거 훅이 둘 다 지워야 한다(대응이 어긋나면 훅은 조용히 아무것도 못 지운다).
WINDOWS_TARGETS = (SERVICE, f"{ACCOUNT}@{SERVICE}")

_backend_cache = None


def platform_backend():
    """이 플랫폼에서 쓸 keyring 백엔드 인스턴스(자동 탐색을 쓰지 않는다).

    모듈 docstring의 경고 참고. 임포트는 함수 안에서 한다 — 백엔드 모듈이 플랫폼별
    네이티브 바인딩을 끌어오므로 다른 OS에서는 임포트 자체가 의미 없다.
    """
    if sys.platform == "darwin":
        from keyring.backends import macOS

        return macOS.Keyring()
    if sys.platform == "win32":
        from keyring.backends import Windows

        return Windows.WinVaultKeyring()
    from keyring.backends import SecretService

    return SecretService.Keyring()


def _backend():
    global _backend_cache
    if _backend_cache is None:
        _backend_cache = platform_backend()
    return _backend_cache


def secret_size_bytes(secret: str) -> int:
    """보관소가 실제로 담게 될 바이트 수.

    글자 수가 아니라 UTF-16 바이트로 센다 — Windows의 한도가 그 단위다.
    """
    return len(secret.encode("utf-16-le"))


def save(secret: str) -> None:
    """자격증명을 보관소에 기록한다.

    Raises:
        CredentialStoreError: 크기 한도를 넘었거나 보관소에 접근하지 못했을 때.
            저장 실패를 삼키면 사용자가 로그인됐다고 오해하므로 반드시 올린다.
    """
    size = secret_size_bytes(secret)
    if size > MAX_SECRET_BYTES:
        raise CredentialStoreError(
            f"자격증명이 보관소 한도를 넘습니다({size} > {MAX_SECRET_BYTES} 바이트). "
            "쿠키 수가 너무 많습니다."
        )
    try:
        _backend().set_password(SERVICE, ACCOUNT, secret)
    except Exception as exc:  # noqa: BLE001 - 백엔드마다 예외 종류가 다르다.
        raise CredentialStoreError(f"자격증명을 보관소에 저장하지 못했습니다: {exc}") from exc


def load() -> str | None:
    """보관된 자격증명(없거나 읽지 못하면 ``None``).

    **예외를 올리지 않는다.** 사용자가 키체인 접근을 거부해도 앱은 "로그인 필요" 상태로
    계속 동작해야 한다. 빈 문자열도 미저장으로 취급한다 — 그러지 않으면 GUI가 "저장된
    쿠키: 있음"으로 잘못 표시한다.
    """
    try:
        secret = _backend().get_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001
        logger.debug("자격증명 보관소를 읽지 못했습니다", exc_info=True)
        return None
    return secret or None


def delete() -> None:
    """보관된 자격증명을 지운다. 없어도, 접근하지 못해도 조용히 넘어간다.

    제거 훅과 이관 경로에서 불리므로 실패가 밖으로 새면 안 된다.
    """
    try:
        _backend().delete_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001
        logger.debug("자격증명 삭제를 건너뜀(항목 없음 또는 접근 불가)", exc_info=True)
