"""평문 쿠키 파일 → OS 보관소 이관 테스트.

v0.1.1까지는 세션 쿠키를 ``cafe_cookie.txt``에 평문으로 저장했다. 그 파일이 남아 있는 한
자격증명 노출은 그대로이므로, 앱이 시작할 때 보관소로 옮기고 **파일을 지운다**.

이관 시점이 '설치 시'가 아니라 '앱 시작 시'인 이유: OTA 업데이트는 새 설치가 아니어서
설치 시점에만 걸면 기존 사용자 대부분이 누락된다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from naver_post_crawler import cookie as cookie_mod
from naver_post_crawler import credentials
from naver_post_crawler.errors import CredentialStoreError

from .test_credentials import BrokenKeyring, FakeKeyring


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """앱 데이터 폴더를 tmp_path로 돌린다(평문 파일이 놓이는 곳)."""
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    backend = FakeKeyring()
    monkeypatch.setattr(credentials, "_backend_cache", backend)
    return backend


def _write_legacy(storage: Path, value: str) -> Path:
    path = storage / cookie_mod.LEGACY_COOKIE_FILE
    path.write_text(value, encoding="utf-8")
    return path


def test_legacy_file_is_moved_into_the_store(storage: Path, fake: FakeKeyring) -> None:
    # covers: Test-8
    _write_legacy(storage, "NID_AUT=a; NID_SES=b")

    cookie_mod.migrate_legacy_cookie()

    assert credentials.load() == "NID_AUT=a; NID_SES=b"


def test_legacy_file_is_gone_after_migration(storage: Path, fake: FakeKeyring) -> None:
    # covers: Test-9 ('옮겼다'만으로는 부족하다 — 원본이 남으면 노출은 그대로다)
    path = _write_legacy(storage, "NID_AUT=a")

    cookie_mod.migrate_legacy_cookie()

    assert not path.exists()


def test_legacy_file_is_deleted_even_when_the_store_fails(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-10 (D-3: 노출 제거를 로그인 유지보다 우선한다. 대가는 재로그인 1회)
    monkeypatch.setattr(credentials, "_backend_cache", BrokenKeyring())
    path = _write_legacy(storage, "NID_AUT=a")

    cookie_mod.migrate_legacy_cookie()  # 예외가 새어 나가면 앱이 시작하지 못한다

    assert not path.exists()


def test_migration_is_a_noop_without_a_legacy_file(storage: Path, fake: FakeKeyring) -> None:
    # covers: Test-11
    cookie_mod.migrate_legacy_cookie()
    cookie_mod.migrate_legacy_cookie()  # 두 번 실행해도 결과가 같다

    assert credentials.load() is None
    assert not (storage / cookie_mod.LEGACY_COOKIE_FILE).exists()


def test_migration_does_not_overwrite_a_newer_stored_value(
    storage: Path, fake: FakeKeyring
) -> None:
    # covers: Test-12 (새 로그인으로 얻은 최신 쿠키를 옛 파일이 되돌리면 안 된다)
    credentials.save("NID_AUT=new")
    path = _write_legacy(storage, "NID_AUT=old")

    cookie_mod.migrate_legacy_cookie()

    assert credentials.load() == "NID_AUT=new"
    assert not path.exists(), "덮어쓰지 않더라도 평문 파일은 지워야 한다"


def test_save_cookie_goes_to_the_store_not_a_file(storage: Path, fake: FakeKeyring) -> None:
    # covers: Test-6
    cookie_mod.save_cookie("  NID_AUT=a; NID_SES=b  \n")

    assert cookie_mod.load_cookie() == "NID_AUT=a; NID_SES=b", "앞뒤 공백은 제거한다"
    assert not (storage / cookie_mod.LEGACY_COOKIE_FILE).exists()


def test_save_cookie_propagates_store_failure(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: Test-5 (GUI가 사용자에게 알릴 수 있어야 한다 — 조용히 삼키지 않는다)
    monkeypatch.setattr(credentials, "_backend_cache", BrokenKeyring())

    with pytest.raises(CredentialStoreError):
        cookie_mod.save_cookie("NID_AUT=a")
