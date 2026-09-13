"""Auth hardening: Quick Connect poll capability + KOSync create binding.

Covers the fixes from the 2026-07-03 auth surface review:
- Quick Connect: polling requires the poll_token issued at initiate, so the
  6-character display code can no longer be brute-forced into a login JWT.
- KOSync /users/create (unauthenticated) never binds the new sync credential
  to an existing Tome account; linking is the authenticated Settings path.
"""
import hashlib
from datetime import datetime, timedelta

from starlette.testclient import TestClient

from backend.models.kosync import KOSyncUser
from backend.models.quick_connect import QuickConnectCode


# ---------------------------------------------------------------------------
# Quick Connect
# ---------------------------------------------------------------------------

class TestQuickConnectPollToken:
    def test_initiate_returns_poll_token(self, client: TestClient):
        resp = client.post("/api/auth/quick-connect/initiate")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["code"]) == 6
        assert len(data["poll_token"]) >= 32

    def test_full_flow_with_token(self, client: TestClient, admin_user):
        user, _ = admin_user
        init = client.post("/api/auth/quick-connect/initiate").json()

        # Pending until authorized
        pending = client.post("/api/auth/quick-connect/poll",
                              json={"code": init["code"], "poll_token": init["poll_token"]})
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"

        # Authorize from the logged-in device (client carries the admin JWT)
        auth = client.post("/api/auth/quick-connect/authorize", json={"code": init["code"]})
        assert auth.status_code == 200

        done = client.post("/api/auth/quick-connect/poll",
                           json={"code": init["code"], "poll_token": init["poll_token"]})
        assert done.status_code == 200
        body = done.json()
        assert body["status"] == "authorized"
        assert body["access_token"]

    def test_poll_with_wrong_token_is_indistinguishable_from_unknown_code(
        self, client: TestClient
    ):
        init = client.post("/api/auth/quick-connect/initiate").json()
        client.post("/api/auth/quick-connect/authorize", json={"code": init["code"]})

        # Correct code, wrong token: the brute-forcer's position. Must get the
        # same 410 as a nonexistent code — no oracle, no JWT.
        wrong = client.post("/api/auth/quick-connect/poll",
                            json={"code": init["code"], "poll_token": "A" * 43})
        unknown = client.post("/api/auth/quick-connect/poll",
                              json={"code": "ZZZZZZ", "poll_token": "A" * 43})
        assert wrong.status_code == 410
        assert unknown.status_code == 410
        assert wrong.json() == unknown.json()

        # The code is still consumable by the legitimate holder afterwards.
        ok = client.post("/api/auth/quick-connect/poll",
                         json={"code": init["code"], "poll_token": init["poll_token"]})
        assert ok.status_code == 200
        assert ok.json()["status"] == "authorized"

    def test_legacy_tokenless_rows_cannot_be_polled(self, client: TestClient, db, admin_user):
        user, _ = admin_user
        # A pre-migration row (poll_token NULL) that is already authorized.
        entry = QuickConnectCode(
            code="ABCDEF", poll_token=None, user_id=user.id,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            authorized_at=datetime.utcnow(),
        )
        db.add(entry)
        db.commit()

        resp = client.post("/api/auth/quick-connect/poll",
                           json={"code": "ABCDEF", "poll_token": ""})
        assert resp.status_code == 410

    def test_old_get_poll_route_yields_no_jwt(self, client: TestClient):
        # The tokenless GET route is removed; unmatched GETs fall through to
        # the SPA catch-all. Whatever comes back, it must never carry a JWT.
        init = client.post("/api/auth/quick-connect/initiate").json()
        client.post("/api/auth/quick-connect/authorize", json={"code": init["code"]})
        resp = client.get(f"/api/auth/quick-connect/poll/{init['code']}")
        assert "access_token" not in resp.text

    def test_expired_code_polls_410(self, client: TestClient, db):
        init = client.post("/api/auth/quick-connect/initiate").json()
        entry = db.query(QuickConnectCode).filter(QuickConnectCode.code == init["code"]).first()
        entry.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()

        resp = client.post("/api/auth/quick-connect/poll",
                           json={"code": init["code"], "poll_token": init["poll_token"]})
        assert resp.status_code == 410


class TestQuickConnectIssuedCodes:
    """"Connect a phone": the signed-in browser issues a pre-authorized code and
    shows it as a QR; the phone polls once and is in. The issuer can watch the
    code's state and cancel it early."""

    def test_issued_code_is_pre_authorized_and_single_use(self, client: TestClient):
        issued = client.post("/api/auth/quick-connect/issue")
        assert issued.status_code == 200
        body = issued.json()
        assert len(body["code"]) == 6
        assert len(body["poll_token"]) >= 32

        done = client.post("/api/auth/quick-connect/poll",
                           json={"code": body["code"], "poll_token": body["poll_token"]})
        assert done.status_code == 200
        assert done.json()["status"] == "authorized"
        assert done.json()["access_token"]

        # consumed: a second poll is gone, and so is the status view
        again = client.post("/api/auth/quick-connect/poll",
                            json={"code": body["code"], "poll_token": body["poll_token"]})
        assert again.status_code == 410
        assert client.get(f"/api/auth/quick-connect/{body['code']}").status_code == 404

    def test_status_then_cancel(self, client: TestClient):
        body = client.post("/api/auth/quick-connect/issue").json()

        st = client.get(f"/api/auth/quick-connect/{body['code']}")
        assert st.status_code == 200
        assert st.json()["status"] == "authorized"

        assert client.delete(f"/api/auth/quick-connect/{body['code']}").status_code == 204
        assert client.get(f"/api/auth/quick-connect/{body['code']}").status_code == 404
        gone = client.post("/api/auth/quick-connect/poll",
                           json={"code": body["code"], "poll_token": body["poll_token"]})
        assert gone.status_code == 410

    def test_unknown_or_unowned_code_is_404(self, client: TestClient, db):
        assert client.get("/api/auth/quick-connect/ZZZZZZ").status_code == 404
        assert client.delete("/api/auth/quick-connect/ZZZZZZ").status_code == 404

        # A pending code initiated by some other device is not ours to inspect or cancel
        init = client.post("/api/auth/quick-connect/initiate").json()
        assert client.get(f"/api/auth/quick-connect/{init['code']}").status_code == 404
        assert client.delete(f"/api/auth/quick-connect/{init['code']}").status_code == 404
        # ...and it still works for its owner
        pending = client.post("/api/auth/quick-connect/poll",
                              json={"code": init["code"], "poll_token": init["poll_token"]})
        assert pending.status_code == 200 and pending.json()["status"] == "pending"

    def test_issue_requires_auth(self, client: TestClient):
        resp = client.post("/api/auth/quick-connect/issue", headers={"Authorization": ""})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# KOSync registration
# ---------------------------------------------------------------------------

class TestKOSyncCreateBinding:
    def test_create_does_not_bind_to_existing_tome_account(
        self, client: TestClient, db, admin_user
    ):
        user, _ = admin_user
        # Attacker registers a KOSync credential under the victim's username.
        resp = client.post("/api/v1/users/create",
                           json={"username": user.username, "password": "attacker-md5"})
        assert resp.status_code == 201

        ks = db.query(KOSyncUser).filter(KOSyncUser.username == user.username).first()
        assert ks is not None
        assert ks.user_id is None  # NOT linked to the Tome account

    def test_settings_register_reclaims_squatted_username(
        self, client: TestClient, db, admin_user
    ):
        user, _ = admin_user
        client.post("/api/v1/users/create",
                    json={"username": user.username, "password": "attacker-md5"})

        # The real user links from Settings (authenticated): the credential is
        # re-keyed to their password and bound to their account.
        resp = client.post("/api/auth/me/kosync", json={"password": "devicepass"})
        assert resp.status_code == 201

        ks = db.query(KOSyncUser).filter(KOSyncUser.username == user.username).first()
        assert ks.user_id == user.id
        assert ks.userkey == hashlib.md5(b"devicepass").hexdigest()

    def test_duplicate_create_still_402(self, client: TestClient):
        client.post("/api/v1/users/create", json={"username": "reader", "password": "k1"})
        dup = client.post("/api/v1/users/create", json={"username": "reader", "password": "k2"})
        assert dup.status_code == 402
