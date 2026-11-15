"""A plugin download mints a key; earlier download keys that no device ever
used are pruned so repeated downloads do not pile up keys (review U1)."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.tome_sync import ApiKey
from backend.models.user import User


def _key(db: Session, user_id: int, label: str, age: timedelta, used: bool = False) -> ApiKey:
    plaintext = ApiKey.generate()
    now = datetime.utcnow()
    k = ApiKey(
        user_id=user_id,
        key_hash=ApiKey.hash_key(plaintext),
        key_prefix=plaintext[:11],
        label=label,
        created_at=now - age,
        last_used_at=(now - timedelta(minutes=5)) if used else None,
    )
    db.add(k)
    db.flush()
    return k


def _labels(db: Session, user_id: int) -> list[str]:
    db.expire_all()
    return sorted(k.label for k in db.query(ApiKey).filter(ApiKey.user_id == user_id).all())


def test_download_prunes_unused_old_download_keys(client: TestClient, db: Session, admin_user):
    user, _ = admin_user
    old_legacy = _key(db, user.id, "KOReader Plugin", timedelta(days=9))
    old_dated = _key(db, user.id, "Plugin download 2026-11-01", timedelta(days=12))
    used_legacy = _key(db, user.id, "KOReader Plugin", timedelta(days=30), used=True)
    used_dated = _key(db, user.id, "Plugin download 2026-10-01", timedelta(days=40), used=True)
    fresh = _key(db, user.id, "Plugin download 2026-11-13", timedelta(days=3))
    paired = _key(db, user.id, "KOReader Plugin (Kindle, paired)", timedelta(days=3))
    manual = _key(db, user.id, "Scripts", timedelta(days=3))
    kept_ids = {used_legacy.id, used_dated.id, fresh.id, paired.id, manual.id}
    gone_ids = {old_legacy.id, old_dated.id}

    r = client.get("/api/plugin/koreader")
    assert r.status_code == 200

    db.expire_all()
    ids = {k.id for k in db.query(ApiKey).filter(ApiKey.user_id == user.id).all()}
    assert kept_ids <= ids
    assert not (gone_ids & ids)
    # Exactly one new key, labelled with today's date.
    new = ids - kept_ids
    assert len(new) == 1
    new_key = db.get(ApiKey, new.pop())
    assert new_key.label == f"Plugin download {datetime.utcnow().date().isoformat()}"
    assert new_key.last_used_at is None


def test_download_leaves_other_users_keys_alone(client: TestClient, db: Session):
    other = User(
        username="otherreader",
        email="other@example.com",
        hashed_password=hash_password("pass12345"),
        is_active=True,
        is_admin=False,
        role="member",
        must_change_password=False,
    )
    db.add(other)
    db.flush()
    _key(db, other.id, "KOReader Plugin", timedelta(days=3))

    assert client.get("/api/plugin/koreader").status_code == 200
    assert _labels(db, other.id) == ["KOReader Plugin"]


def test_repeated_downloads_do_not_pile_up(client: TestClient, db: Session, admin_user):
    user, _ = admin_user
    for _ in range(3):
        assert client.get("/api/plugin/koreader").status_code == 200
        # Age every unused key past the window, as if the downloads were days apart.
        for k in db.query(ApiKey).filter(ApiKey.user_id == user.id).all():
            k.created_at = k.created_at - timedelta(days=8)
        db.flush()
    assert len(_labels(db, user.id)) == 1


def test_key_used_by_a_device_survives(client: TestClient, db: Session, admin_user):
    user, _ = admin_user
    plaintext = ApiKey.generate()
    db.add(ApiKey(
        user_id=user.id,
        key_hash=ApiKey.hash_key(plaintext),
        key_prefix=plaintext[:11],
        label="KOReader Plugin",
        created_at=datetime.utcnow() - timedelta(days=5),
    ))
    db.flush()
    # The device signs in once with it, which records last_used_at.
    r = client.get("/api/tome-sync/stats/watermark", headers={"Authorization": f"Bearer {plaintext}"})
    assert r.status_code == 200

    assert client.get("/api/plugin/koreader").status_code == 200
    db.expire_all()
    assert db.query(ApiKey).filter(ApiKey.key_hash == ApiKey.hash_key(plaintext)).count() == 1
