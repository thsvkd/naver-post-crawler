"""평문 쿠키 파일 → OS 보관소 이관 테스트.

v0.1.1까지는 세션 쿠키를 ``cafe_cookie.txt``에 평문으로 저장했다. 그 파일이 남아 있는 한
자격증명 노출은 그대로이므로, 앱이 시작할 때 보관소로 옮기고 **파일을 지운다**.

이관 시점이 '설치 시'가 아니라 '앱 시작 시'인 이유: OTA 업데이트는 새 설치가 아니어서
설치 시점에만 걸면 기존 사용자 대부분이 누락된다.

``covers`` 태그의 번호는 ``docs/handoff-credential-storage.md``의 인수 기준이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from naver_post_crawler import cookie as cookie_mod
from naver_post_crawler import credentials
from naver_post_crawler.errors import CredentialStoreError

from .test_credentials import BrokenKeyring, FakeKeyring, ReadFailsKeyring


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
    # covers: cred/Test-8
    _write_legacy(storage, "NID_AUT=a; NID_SES=b")

    cookie_mod.migrate_legacy_cookie()

    assert credentials.load() == "NID_AUT=a; NID_SES=b"


def test_legacy_file_is_gone_after_migration(storage: Path, fake: FakeKeyring) -> None:
    # covers: cred/Test-9 ('옮겼다'만으로는 부족하다 — 원본이 남으면 노출은 그대로다)
    path = _write_legacy(storage, "NID_AUT=a")

    cookie_mod.migrate_legacy_cookie()

    assert not path.exists()


def test_legacy_file_is_deleted_even_when_the_store_fails(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-10 (D-3: 노출 제거를 로그인 유지보다 우선한다. 대가는 재로그인 1회)
    monkeypatch.setattr(credentials, "_backend_cache", BrokenKeyring())
    path = _write_legacy(storage, "NID_AUT=a")

    cookie_mod.migrate_legacy_cookie()  # 예외가 새어 나가면 앱이 시작하지 못한다

    assert not path.exists()


def test_migration_is_a_noop_without_a_legacy_file(storage: Path, fake: FakeKeyring) -> None:
    # covers: cred/Test-11
    cookie_mod.migrate_legacy_cookie()
    cookie_mod.migrate_legacy_cookie()  # 두 번 실행해도 결과가 같다

    assert credentials.load() is None
    assert not (storage / cookie_mod.LEGACY_COOKIE_FILE).exists()


def test_migration_does_not_overwrite_a_newer_stored_value(
    storage: Path, fake: FakeKeyring
) -> None:
    # covers: cred/Test-12 (새 로그인으로 얻은 최신 쿠키를 옛 파일이 되돌리면 안 된다)
    credentials.save("NID_AUT=new")
    path = _write_legacy(storage, "NID_AUT=old")

    cookie_mod.migrate_legacy_cookie()

    assert credentials.load() == "NID_AUT=new"
    assert not path.exists(), "덮어쓰지 않더라도 평문 파일은 지워야 한다"


def test_save_cookie_goes_to_the_store_not_a_file(storage: Path, fake: FakeKeyring) -> None:
    # covers: cred/Test-6
    cookie_mod.save_cookie("  NID_AUT=a; NID_SES=b  \n")

    assert cookie_mod.load_cookie() == "NID_AUT=a; NID_SES=b", "앞뒤 공백은 제거한다"
    assert not (storage / cookie_mod.LEGACY_COOKIE_FILE).exists()


def test_save_cookie_propagates_store_failure(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-5 (GUI가 사용자에게 알릴 수 있어야 한다 — 조용히 삼키지 않는다)
    monkeypatch.setattr(credentials, "_backend_cache", BrokenKeyring())

    with pytest.raises(CredentialStoreError):
        cookie_mod.save_cookie("NID_AUT=a")


# -- 이관 결과 보고 -----------------------------------------------------------
# 이관은 로깅이 설정되기 전에 돈다. 결과를 돌려주지 않으면 실패가 로그에도 화면에도
# 남지 않아, 사용자는 업데이트 후 갑자기 로그아웃된 이유를 알 방법이 없다.


def test_migration_reports_moved(storage: Path, fake: FakeKeyring) -> None:
    # covers: cred/Test-8
    _write_legacy(storage, "NID_AUT=a")

    assert cookie_mod.migrate_legacy_cookie().outcome is cookie_mod.CookieMigration.MOVED


def test_migration_reports_nothing_without_a_legacy_file(storage: Path, fake: FakeKeyring) -> None:
    # covers: cred/Test-11
    assert cookie_mod.migrate_legacy_cookie().outcome is cookie_mod.CookieMigration.NOTHING


def test_migration_reports_kept_when_the_store_already_has_a_value(
    storage: Path, fake: FakeKeyring
) -> None:
    # covers: cred/Test-12
    credentials.save("NID_AUT=new")
    _write_legacy(storage, "NID_AUT=old")

    assert cookie_mod.migrate_legacy_cookie().outcome is cookie_mod.CookieMigration.KEPT


def test_migration_reports_lost_when_the_store_write_fails(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-10 (D-3의 대가를 사용자에게 알릴 수 있어야 한다)
    monkeypatch.setattr(credentials, "_backend_cache", BrokenKeyring())
    _write_legacy(storage, "NID_AUT=a")

    assert cookie_mod.migrate_legacy_cookie().outcome is cookie_mod.CookieMigration.LOST


def test_empty_legacy_file_is_not_reported_as_a_loss(storage: Path, fake: FakeKeyring) -> None:
    # covers: cred/Test-11
    # 옛 빈 파일 하나 때문에 "다시 로그인하라"는 잘못된 안내가 나가면 안 된다.
    path = _write_legacy(storage, "   \n")

    assert cookie_mod.migrate_legacy_cookie().outcome is cookie_mod.CookieMigration.NOTHING
    assert not path.exists()


def test_unreadable_store_does_not_get_overwritten_by_the_legacy_value(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12
    # 읽기는 거부되고 쓰기는 되는 상태(서명 신원이 바뀐 macOS)에서, 읽기 실패를 "값 없음"
    # 으로 오해하면 최신 쿠키를 옛 평문으로 덮어쓴다. Test-12가 지키려는 불변식이다.
    backend = ReadFailsKeyring()
    backend.store[(credentials.SERVICE, credentials.ACCOUNT)] = "NID_AUT=new"
    monkeypatch.setattr(credentials, "_backend_cache", backend)
    path = _write_legacy(storage, "NID_AUT=old")

    result = cookie_mod.migrate_legacy_cookie()

    assert backend.store[(credentials.SERVICE, credentials.ACCOUNT)] == "NID_AUT=new"
    assert result.outcome is cookie_mod.CookieMigration.LOST
    assert not path.exists(), "읽지 못했더라도 평문은 지운다(D-3)"


def test_unlink_failure_is_reported_as_exposed(
    storage: Path, fake: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b
    # 백신·인덱서·다른 인스턴스가 파일을 잡고 있으면 실제로 일어난다. 보관소 저장이
    # 성공했다는 이유로 노출을 감추면, 이 작업이 없애려던 평문이 조용히 남는다.
    path = _write_legacy(storage, "NID_AUT=a")

    def boom(self: Path, **_kwargs: object) -> None:
        raise PermissionError("파일이 사용 중입니다")

    monkeypatch.setattr(Path, "unlink", boom)

    result = cookie_mod.migrate_legacy_cookie()

    assert result.exposed is True
    assert result.outcome is cookie_mod.CookieMigration.MOVED, "보관소 저장은 성공했다"
    assert path.exists()
    assert credentials.load() == "NID_AUT=a"


def test_loss_is_not_swallowed_by_an_exposure(
    storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # covers: cred/Test-12b
    # 저장도 실패하고 삭제도 실패한 조합. 노출만 알리면 사용자는 로그아웃된 줄 모른 채
    # 안내대로 파일을 지워, 다음 실행에서 이관될 수도 있었던 유일한 사본을 없앤다.
    monkeypatch.setattr(credentials, "_backend_cache", BrokenKeyring())
    _write_legacy(storage, "NID_AUT=a")

    def boom(self: Path, **_kwargs: object) -> None:
        raise PermissionError("파일이 사용 중입니다")

    monkeypatch.setattr(Path, "unlink", boom)

    result = cookie_mod.migrate_legacy_cookie()

    assert result.exposed is True
    assert result.outcome is cookie_mod.CookieMigration.LOST, "손실이 노출에 가려지면 안 된다"


def test_advice_puts_re_login_before_deletion_when_both_happened(tmp_path: Path) -> None:
    # covers: cred/Test-12b
    result = cookie_mod.MigrationResult(
        cookie_mod.CookieMigration.LOST, exposed=True, path=tmp_path / "cafe_cookie.txt"
    )

    advice = cookie_mod.migration_advice(result)

    assert advice is not None
    assert advice.index("네이버 로그인") < advice.index("지워"), "재로그인이 삭제보다 먼저다"


def test_advice_is_silent_when_nothing_needs_saying(tmp_path: Path) -> None:
    # covers: cred/Test-12b (평문 파일이 없던 대부분의 실행에서 경고가 나가면 안 된다)
    result = cookie_mod.MigrationResult(
        cookie_mod.CookieMigration.NOTHING, exposed=False, path=tmp_path / "cafe_cookie.txt"
    )

    assert cookie_mod.migration_advice(result) is None
