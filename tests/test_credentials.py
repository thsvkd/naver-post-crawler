"""자격증명 OS 보관소 계층(credentials) 테스트.

세션 쿠키는 로그인 그 자체다. 평문 파일로 두면 앱을 지워도 디스크에 남고 평소에도
노출된 상태가 되므로, macOS 키체인 / Windows 자격 증명 관리자에 넣는다.

실제 보관소는 헤드리스/CI에서 잠겨 있거나 없으므로, 여기서는 가짜 백엔드를 주입해
**우리 계층의 계약**만 검증한다. 실제 백엔드가 배포본에서 잡히는지는 E2E 실측 항목이다.

``covers`` 태그의 번호는 ``docs/handoff-credential-storage.md``의 인수 기준이다.
"""

from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path

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
    # covers: cred/Test-1
    credentials.save("NID_AUT=a; NID_SES=b")

    assert credentials.load() == "NID_AUT=a; NID_SES=b"


def test_load_without_saving_returns_none(fake: FakeKeyring) -> None:
    # covers: cred/Test-2
    assert credentials.load() is None


def test_load_returns_none_for_empty_stored_value(fake: FakeKeyring) -> None:
    # covers: cred/Test-2 (빈 값과 미저장을 구분하지 못하면 GUI가 '로그인됨'으로 잘못 표시한다)
    fake.store[(credentials.SERVICE, credentials.ACCOUNT)] = ""

    assert credentials.load() is None


def test_delete_removes_the_secret(fake: FakeKeyring) -> None:
    # covers: cred/Test-3
    credentials.save("NID_AUT=a")
    credentials.delete()

    assert credentials.load() is None


def test_delete_is_idempotent(fake: FakeKeyring) -> None:
    # covers: cred/Test-3 (제거 훅이 두 번 실행돼도 실패하면 안 된다)
    credentials.delete()
    credentials.delete()  # 없는 항목 삭제도 오류가 아니다


def test_backend_is_chosen_explicitly_per_platform() -> None:
    # covers: cred/Test-4
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
    # covers: cred/Test-4
    # 번들에는 dist-info가 없을 수 있고, 그러면 엔트리포인트 탐색이 조용히 빈 백엔드를
    # 고른다(개발에서는 통과, 배포본에서만 실패). 탐색 경로를 아예 안 타는지 고정한다.
    def explode() -> None:
        raise AssertionError("keyring 자동 탐색(get_keyring)을 호출하면 안 된다")

    monkeypatch.setattr(keyring, "get_keyring", explode)

    credentials.save("NID_AUT=a")
    assert credentials.load() == "NID_AUT=a"
    credentials.delete()


def test_platform_backend_does_not_use_autodiscovery(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: cred/Test-4
    def explode() -> None:
        raise AssertionError("keyring 자동 탐색(get_keyring)을 호출하면 안 된다")

    monkeypatch.setattr(keyring, "get_keyring", explode)

    credentials.platform_backend()


def test_load_returns_none_when_backend_unavailable(broken: None) -> None:
    # covers: cred/Test-5 (키체인 접근을 거부해도 앱은 '로그인 필요' 상태로 계속 동작해야 한다)
    assert credentials.load() is None


def test_delete_survives_unavailable_backend(broken: None) -> None:
    # covers: cred/Test-5
    credentials.delete()  # 예외가 새어 나가면 제거 훅이 실패한다


def test_save_raises_domain_error_when_backend_unavailable(broken: None) -> None:
    # covers: cred/Test-5 (저장 실패는 조용히 넘기지 않고 GUI가 표시할 수 있는 형태로 올린다)
    with pytest.raises(CredentialStoreError):
        credentials.save("NID_AUT=a")


def test_saving_never_writes_the_secret_to_a_file(
    fake: FakeKeyring, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-6 (이번 작업의 존재 이유 — 평문 파일 저장이 회귀로 되살아나면 안 된다)
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path))
    secret = "NID_AUT=SECRETVALUE; NID_SES=OTHERSECRET"

    credentials.save(secret)

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes().decode("utf-8", "ignore"), (
                f"자격증명이 파일에 기록되었다: {path}"
            )


def test_oversized_secret_is_rejected_before_reaching_the_backend(fake: FakeKeyring) -> None:
    # covers: cred/Test-7
    # Windows 자격 증명 관리자의 blob 한도는 2560바이트이고 UTF-16 저장이라 실질 절반이다.
    # 넘으면 조용히 실패해 '로그인은 했는데 저장이 안 되는' 증상이 된다.
    oversized = "x" * (credentials.MAX_SECRET_BYTES // 2 + 1)

    with pytest.raises(CredentialStoreError):
        credentials.save(oversized)

    assert fake.store == {}, "한도 초과 값이 백엔드까지 갔다"


def test_secret_at_the_limit_is_accepted(fake: FakeKeyring) -> None:
    # covers: cred/Test-7 (경계에서 멀쩡한 값을 거부하면 사용자가 로그인할 수 없다)
    at_limit = "x" * (credentials.MAX_SECRET_BYTES // 2)

    credentials.save(at_limit)

    assert credentials.load() == at_limit


def test_size_limit_counts_utf16_bytes_not_characters(fake: FakeKeyring) -> None:
    # covers: cred/Test-7
    # 한글도 BMP라 UTF-16에서 2바이트다. 문자 수로 세면 한도를 잘못 계산한다.
    assert credentials.secret_size_bytes("가나다") == 6
    assert credentials.secret_size_bytes("abc") == 6


class ReadFailsKeyring(FakeKeyring):
    """읽기만 거부하는 보관소.

    macOS에서 서명 신원이 바뀌면 기존 항목 읽기가 거부되면서 쓰기는 되는 상태가 나온다.
    이 조합이 "저장된 값 없음"과 구분되지 않으면 이관이 최신 값을 옛 평문으로 덮어쓴다.
    """

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringError("읽기 거부")


def test_load_strict_raises_when_the_store_cannot_be_read(broken: None) -> None:
    # covers: cred/Test-5 (호출자가 '값 없음'과 '읽지 못함'을 구분할 수 있어야 한다)
    with pytest.raises(CredentialStoreError):
        credentials.load_strict()


def test_load_strict_returns_none_when_nothing_is_stored(fake: FakeKeyring) -> None:
    # covers: cred/Test-2
    assert credentials.load_strict() is None


def test_unreadable_store_is_logged_at_warning(
    broken: None, caplog: pytest.LogCaptureFixture
) -> None:
    # covers: cred/Test-5
    # DEBUG로 두면 기본 레벨(INFO)에서 사라져, 키체인 접근 거부가 어디에도 흔적을 남기지
    # 않는다. 사용자에게는 "저장된 쿠키: 없음"으로만 보이므로 로그가 유일한 단서다.
    with caplog.at_level("WARNING", logger="naver_post_crawler.credentials"):
        assert credentials.load() is None

    assert caplog.records, "읽기 실패가 WARNING 이상으로 남아야 한다"


def test_save_failure_message_warns_that_the_previous_value_may_be_gone(broken: None) -> None:
    # covers: cred/Test-5
    # macOS 백엔드는 지우고 다시 추가한다(실측: keyring 25.7.0 backends/macOS/api.py).
    # 추가 단계에서 실패하면 이전 쿠키도 이미 사라진 상태라, "실패했으니 예전 것은 남았겠지"
    # 라는 오해를 문구가 막아야 한다.
    with pytest.raises(CredentialStoreError) as excinfo:
        credentials.save("NID_AUT=a")

    assert "다시 로그인" in str(excinfo.value)


def test_oversized_secret_error_names_an_in_app_remedy(fake: FakeKeyring) -> None:
    # covers: cred/Test-7
    # 한도 초과를 알리기만 하고 방법을 주지 않으면 사용자는 앱 안에서 할 수 있는 일이 없다.
    with pytest.raises(CredentialStoreError) as excinfo:
        credentials.save("x" * (credentials.MAX_SECRET_BYTES // 2 + 1))

    assert "네이버 로그인" in str(excinfo.value)


def test_saving_performs_no_file_writes_at_all(
    fake: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-6
    # 디렉터리를 훑어 비밀값을 찾는 방식은 **훑는 곳에만** 효력이 있다. 저장 경로가 다른
    # 위치(임시폴더 등)에 평문을 쓰도록 회귀해도 그런 테스트는 통과한다. 여기서는 저장
    # 경로가 파일을 **여는 행위 자체**를 금지해, 기록 위치와 무관하게 잡는다.
    def forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"자격증명 저장 경로가 파일을 열었다: {args!r}")

    monkeypatch.setattr(builtins, "open", forbid)
    monkeypatch.setattr(os, "open", forbid)
    monkeypatch.setattr(Path, "write_text", forbid)
    monkeypatch.setattr(Path, "write_bytes", forbid)
    monkeypatch.setattr(Path, "open", forbid)

    credentials.save("NID_AUT=SECRETVALUE; NID_SES=OTHERSECRET")

    assert fake.store[(credentials.SERVICE, credentials.ACCOUNT)].startswith("NID_AUT=")
