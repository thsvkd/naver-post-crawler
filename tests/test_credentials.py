"""자격증명 OS 보관소 계층(credentials) 테스트.

세션 쿠키는 로그인 그 자체다. 평문 파일로 두면 앱을 지워도 디스크에 남고 평소에도
노출된 상태가 되므로, macOS 키체인 / Windows 자격 증명 관리자에 넣는다.

실제 보관소는 헤드리스/CI에서 잠겨 있거나 없으므로, 여기서는 가짜 백엔드를 주입해
**우리 계층의 계약**만 검증한다. 실제 백엔드가 배포본에서 잡히는지는 E2E 실측 항목이다.
"""

from __future__ import annotations

import sys

import keyring.errors
import pytest

from naver_post_crawler import credentials
from naver_post_crawler.errors import CredentialStoreError


class FakeKeyring:
    """메모리 보관소. keyring 백엔드가 쓰는 세 메서드만 구현한다."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("없는 항목")
        del self.store[(service, username)]


class BrokenKeyring:
    """접근이 거부되거나 백엔드가 없는 상황(사용자가 키체인 접근을 거부한 경우 등)."""

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringError("보관소 접근 거부")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.KeyringError("보관소 접근 거부")

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.KeyringError("보관소 접근 거부")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    backend = FakeKeyring()
    monkeypatch.setattr(credentials, "_backend_cache", backend)
    return backend


@pytest.fixture
def broken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_backend_cache", BrokenKeyring())


def test_save_then_load_returns_same_value(fake: FakeKeyring) -> None:
    # covers: Test-1
    credentials.save("NID_AUT=a; NID_SES=b")

    assert credentials.load() == "NID_AUT=a; NID_SES=b"


def test_load_without_saving_returns_none(fake: FakeKeyring) -> None:
    # covers: Test-2
    assert credentials.load() is None


def test_load_returns_none_for_empty_stored_value(fake: FakeKeyring) -> None:
    # covers: Test-2 (빈 값과 미저장을 구분하지 못하면 GUI가 '로그인됨'으로 잘못 표시한다)
    fake.store[(credentials.SERVICE, credentials.ACCOUNT)] = ""

    assert credentials.load() is None


def test_delete_removes_the_secret(fake: FakeKeyring) -> None:
    # covers: Test-3
    credentials.save("NID_AUT=a")
    credentials.delete()

    assert credentials.load() is None


def test_delete_is_idempotent(fake: FakeKeyring) -> None:
    # covers: Test-3 (제거 훅이 두 번 실행돼도 실패하면 안 된다)
    credentials.delete()
    credentials.delete()  # 없는 항목 삭제도 오류가 아니다


def test_backend_is_chosen_explicitly_per_platform() -> None:
    # covers: Test-4
    backend = credentials.platform_backend()
    module = type(backend).__module__

    if sys.platform == "darwin":
        assert "macOS" in module, f"macOS 백엔드가 아니다: {module}"
    elif sys.platform == "win32":
        assert "Windows" in module, f"Windows 백엔드가 아니다: {module}"
    else:
        assert "SecretService" in module, f"SecretService 백엔드가 아니다: {module}"


def test_backend_never_uses_keyring_autodiscovery(
    fake: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-4
    # 번들에는 dist-info가 없을 수 있고, 그러면 엔트리포인트 탐색이 조용히 빈 백엔드를
    # 고른다(개발에서는 통과, 배포본에서만 실패). 탐색 경로를 아예 안 타는지 고정한다.
    def explode() -> None:
        raise AssertionError("keyring 자동 탐색(get_keyring)을 호출하면 안 된다")

    monkeypatch.setattr(keyring, "get_keyring", explode)

    credentials.save("NID_AUT=a")
    assert credentials.load() == "NID_AUT=a"
    credentials.delete()


def test_platform_backend_does_not_use_autodiscovery(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: Test-4
    def explode() -> None:
        raise AssertionError("keyring 자동 탐색(get_keyring)을 호출하면 안 된다")

    monkeypatch.setattr(keyring, "get_keyring", explode)

    credentials.platform_backend()


def test_load_returns_none_when_backend_unavailable(broken: None) -> None:
    # covers: Test-5 (키체인 접근을 거부해도 앱은 '로그인 필요' 상태로 계속 동작해야 한다)
    assert credentials.load() is None


def test_delete_survives_unavailable_backend(broken: None) -> None:
    # covers: Test-5
    credentials.delete()  # 예외가 새어 나가면 제거 훅이 실패한다


def test_save_raises_domain_error_when_backend_unavailable(broken: None) -> None:
    # covers: Test-5 (저장 실패는 조용히 넘기지 않고 GUI가 표시할 수 있는 형태로 올린다)
    with pytest.raises(CredentialStoreError):
        credentials.save("NID_AUT=a")


def test_saving_never_writes_the_secret_to_a_file(
    fake: FakeKeyring, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-6 (이번 작업의 존재 이유 — 평문 파일 저장이 회귀로 되살아나면 안 된다)
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path))
    secret = "NID_AUT=SECRETVALUE; NID_SES=OTHERSECRET"

    credentials.save(secret)

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes().decode("utf-8", "ignore"), (
                f"자격증명이 파일에 기록되었다: {path}"
            )


def test_oversized_secret_is_rejected_before_reaching_the_backend(fake: FakeKeyring) -> None:
    # covers: Test-7
    # Windows 자격 증명 관리자의 blob 한도는 2560바이트이고 UTF-16 저장이라 실질 절반이다.
    # 넘으면 조용히 실패해 '로그인은 했는데 저장이 안 되는' 증상이 된다.
    oversized = "x" * (credentials.MAX_SECRET_BYTES // 2 + 1)

    with pytest.raises(CredentialStoreError):
        credentials.save(oversized)

    assert fake.store == {}, "한도 초과 값이 백엔드까지 갔다"


def test_secret_at_the_limit_is_accepted(fake: FakeKeyring) -> None:
    # covers: Test-7 (경계에서 멀쩡한 값을 거부하면 사용자가 로그인할 수 없다)
    at_limit = "x" * (credentials.MAX_SECRET_BYTES // 2)

    credentials.save(at_limit)

    assert credentials.load() == at_limit


def test_size_limit_counts_utf16_bytes_not_characters(fake: FakeKeyring) -> None:
    # covers: Test-7
    # 한글도 BMP라 UTF-16에서 2바이트다. 문자 수로 세면 한도를 잘못 계산한다.
    assert credentials.secret_size_bytes("가나다") == 6
    assert credentials.secret_size_bytes("abc") == 6
