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

# keyring의 Windows 백엔드가 실제로 만드는 대상 이름.
#
# 기본 대상은 서비스 이름이다. 그리고 ``set_password``는 저장 전에 기존 항목을 계정으로
# 거르지 않고 읽어, 있으면 그 값을 ``계정@서비스`` 복합 이름으로 **복사해 둔다**. 즉 같은
# 계정으로 두 번째 저장을 하는 순간부터 복합 항목에 직전 세션 쿠키가 남는다(실측:
# keyring 25.7.0 ``backends/Windows.py``의 set_password).
#
# 그래서 지울 때는 반드시 둘 다 지워야 한다. 하나라도 빠지면 제거 후에도 이전 세션
# 자격증명이 자격 증명 관리자에 남는다.
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

        backend = Windows.WinVaultKeyring()
        # keyring 기본값은 CRED_PERSIST_ENTERPRISE다. 그 값은 자격증명을 **로밍 사용자
        # 프로필에 포함**시켜, 도메인 환경(회사 관리 PC)에서 프로필과 함께 다른 기기로
        # 복제된다. 개인 네이버 세션을 회사 프로필 저장소로 퍼뜨리지 않도록 이 기기로
        # 한정한다. 문자열은 keyring이 CRED_PERSIST_LOCAL_MACHINE으로 변환한다.
        backend.persist = "local machine"
        return backend
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
            "브라우저에서 내보낸 쿠키 파일에 광고·추적 쿠키까지 함께 담겼을 때 생깁니다. "
            "앱의 '네이버 로그인' 버튼은 빈 세션에서 로그인해 쿠키가 적으므로 보통 한도 안에 "
            "들어갑니다."
        )
    try:
        _backend().set_password(SERVICE, ACCOUNT, secret)
    except Exception as exc:  # noqa: BLE001 - 백엔드마다 예외 종류가 다르다.
        # macOS 백엔드는 기존 항목을 지운 뒤 새로 추가한다(실측: keyring 25.7.0
        # backends/macOS/api.py의 set_generic_password). 추가 단계에서 실패하면 이전
        # 자격증명도 이미 사라진 상태이므로, "실패했으니 예전 것은 남아 있겠지"라는
        # 오해가 생기지 않도록 문구로 알린다.
        raise CredentialStoreError(
            f"자격증명을 보관소에 저장하지 못했습니다: {exc} "
            "(이전에 저장된 쿠키도 사라졌을 수 있습니다. 다시 로그인해 주세요.)"
        ) from exc


def load_strict() -> str | None:
    """보관된 자격증명. **읽기 실패는 예외로 올린다.**

    "저장된 값이 없다"와 "읽지 못했다"를 구분해야 하는 호출자를 위한 형태다. 둘을 뭉뚱그리면
    이관 로직이 읽기 실패를 "저장된 값 없음"으로 오해해 최신 값을 옛 평문으로 덮어쓴다.

    Raises:
        CredentialStoreError: 보관소에 접근하지 못했을 때.
    """
    try:
        secret = _backend().get_password(SERVICE, ACCOUNT)
    except Exception as exc:  # noqa: BLE001
        raise CredentialStoreError(f"자격증명 보관소를 읽지 못했습니다: {exc}") from exc
    # 빈 문자열도 미저장으로 취급한다 — 그러지 않으면 GUI가 "저장된 쿠키: 있음"으로
    # 잘못 표시한다.
    return secret or None


def load() -> str | None:
    """보관된 자격증명(없거나 읽지 못하면 ``None``).

    **예외를 올리지 않는다.** 사용자가 키체인 접근을 거부해도 앱은 "로그인 필요" 상태로
    계속 동작해야 한다.
    """
    try:
        return load_strict()
    except CredentialStoreError:
        # DEBUG로 두면 기본 레벨(INFO)에서 사라져 접근 거부가 어디에도 흔적을 남기지
        # 않는다. 사용자에게는 "저장된 쿠키: 없음"으로만 보이므로 로그가 유일한 단서다.
        logger.warning("자격증명 보관소를 읽지 못했습니다", exc_info=True)
        return None


def delete() -> None:
    """보관된 자격증명을 지운다. 없어도, 접근하지 못해도 조용히 넘어간다.

    제거 훅과 이관 경로에서 불리므로 실패가 밖으로 새면 안 된다.
    """
    try:
        _backend().delete_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001
        logger.debug("자격증명 삭제를 건너뜀(항목 없음 또는 접근 불가)", exc_info=True)
