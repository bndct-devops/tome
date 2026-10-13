"""Device inventories: which books are on each KOReader device (plugin build 50).

PUT /api/tome-sync/device-books (plugin, API key) replaces a device's list;
GET /api/device-books and the on_device / not_on_device filters of
GET /api/books read it, per user and per named device.
"""
import json

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.core.security import create_access_token, hash_password
from backend.models.device_inventory import DeviceBook, DeviceInventory
from backend.models.library import Library, SavedFilter
from backend.models.user import User, UserPermission


def _key(client: TestClient, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post("/api/plugin/api-keys", json={"label": "inv"}, headers=headers)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['key']}"}


def _put(client, key, device, ids, build=50):
    return client.put("/api/tome-sync/device-books",
                      json={"device": device, "book_ids": ids, "plugin_build": build},
                      headers=key)


def _member(db: Session, name: str = "reader") -> tuple[User, str]:
    u = User(username=name, email=f"{name}@x.io", hashed_password=hash_password("x"),
             is_admin=False, role="member", is_active=True, must_change_password=False)
    db.add(u)
    db.flush()
    db.add(UserPermission(user_id=u.id, can_upload=True, can_download=True))
    db.commit()
    return u, create_access_token(subject=u.id)


def _titles(client, **params) -> set[str]:
    r = client.get("/api/books", params=params)
    assert r.status_code == 200, r.text
    return {b["title"] for b in r.json()}


def test_upload_replaces_the_device_list(client, db, make_book):
    a, b, c = make_book(title="A"), make_book(title="B"), make_book(title="C")
    db.commit()
    key = _key(client)

    r = _put(client, key, "Kindle", [a.id, b.id])
    assert r.status_code == 200 and r.json() == {"stored": 2, "ignored": 0}
    # Next sync: B deleted on the device, C downloaded. Full replace.
    r = _put(client, key, "Kindle", [a.id, c.id])
    assert r.json()["stored"] == 2

    devices = client.get("/api/device-books").json()["devices"]
    assert len(devices) == 1
    d = devices[0]
    assert d["name"] == "Kindle" and d["book_ids"] == sorted([a.id, c.id])
    assert d["book_count"] == 2 and d["plugin_build"] == 50
    assert d["synced_at"].endswith("Z")


def test_empty_list_and_lua_empty_object(client, db, make_book):
    a = make_book(title="A")
    db.commit()
    key = _key(client)
    _put(client, key, "Kindle", [a.id])
    # KOReader's rapidjson sends an empty table as {}.
    r = client.put("/api/tome-sync/device-books",
                   json={"device": "Kindle", "book_ids": {}}, headers=key)
    assert r.status_code == 200 and r.json()["stored"] == 0
    assert client.get("/api/device-books").json()["devices"][0]["book_ids"] == []


def test_device_name_required(client, db):
    key = _key(client)
    assert _put(client, key, "   ", []).status_code == 422


def test_unknown_and_invisible_ids_are_dropped(client, db, admin_user, make_book):
    admin, _ = admin_user
    member, token = _member(db)
    shared = make_book(title="Shared")
    hidden = make_book(title="Hidden")
    lib = Library(name="Admin only", is_public=False, owner_id=admin.id)
    db.add(lib)
    db.flush()
    hidden.libraries.append(lib)
    db.commit()

    key = _key(client, token)
    r = _put(client, key, "Boox", [shared.id, hidden.id, 999999])
    assert r.json() == {"stored": 1, "ignored": 2}
    assert db.query(DeviceBook).count() == 1


def test_lists_are_per_user_and_per_device(client, db, make_book):
    a, b = make_book(title="A"), make_book(title="B")
    db.commit()
    member, token = _member(db)
    admin_key = _key(client)
    member_key = _key(client, token)
    _put(client, admin_key, "Kindle", [a.id])
    _put(client, admin_key, "Phone", [b.id])
    _put(client, member_key, "Kindle", [a.id, b.id])

    mine = {d["name"]: d["book_ids"] for d in client.get("/api/device-books").json()["devices"]}
    assert mine == {"Kindle": [a.id], "Phone": [b.id]}
    theirs = client.get("/api/device-books", headers={"Authorization": f"Bearer {token}"}).json()
    assert [(d["name"], d["book_ids"]) for d in theirs["devices"]] == [("Kindle", sorted([a.id, b.id]))]


def test_books_filter_on_and_not_on_one_device(client, db, make_book):
    a, b, c = make_book(title="A"), make_book(title="B"), make_book(title="C")
    db.commit()
    key = _key(client)
    _put(client, key, "Kindle", [a.id, b.id])
    _put(client, key, "Phone", [c.id])

    assert _titles(client, on_device="Kindle") == {"A", "B"}
    assert _titles(client, not_on_device="Kindle") == {"C"}
    assert _titles(client, on_device="Phone") == {"C"}
    # Device-specific: "not on the Kindle" does not care about the phone.
    assert "C" in _titles(client, not_on_device="Kindle")
    # Unknown device: nothing is on it, everything is not on it.
    assert _titles(client, on_device="Nope") == set()
    assert _titles(client, not_on_device="Nope") == {"A", "B", "C"}
    # Several devices, comma-separated: on every "on" device, on no "not on" one.
    _put(client, key, "Boox", [a.id, c.id])
    assert _titles(client, on_device="Kindle,Boox") == {"A"}
    assert _titles(client, on_device="Kindle", not_on_device="Boox") == {"B"}
    assert _titles(client, not_on_device="Kindle,Boox") == set()
    assert _titles(client, on_device=" Kindle , ") == {"A", "B"}
    _put(client, key, "Boox", [])
    # The ids endpoint (select-all) takes the same filter.
    ids = client.get("/api/books/ids", params={"on_device": "Kindle"}).json()["ids"]
    assert sorted(ids) == sorted([a.id, b.id])


def test_filter_never_uses_another_users_device(client, db, make_book):
    a = make_book(title="A")
    db.commit()
    member, token = _member(db)
    _put(client, _key(client, token), "Kindle", [a.id])
    # The admin has no device called Kindle: their filter sees nothing on it.
    assert _titles(client, on_device="Kindle") == set()


def test_forget_device(client, db, make_book):
    a = make_book(title="A")
    db.commit()
    key = _key(client)
    _put(client, key, "Old Kobo", [a.id])
    assert client.delete("/api/device-books/Old Kobo").status_code == 204
    assert client.get("/api/device-books").json()["devices"] == []
    assert db.query(DeviceBook).count() == 0
    assert client.delete("/api/device-books/Old Kobo").status_code == 404


def test_rename_moves_the_inventory(client, db, make_book):
    a, b = make_book(title="A"), make_book(title="B")
    db.commit()
    key = _key(client)
    _put(client, key, "KindlePaperWhite4", [a.id])
    r = client.post("/api/tome-sync/stats/rename-device",
                    json={"from_device": "KindlePaperWhite4", "to_device": "Kindle"}, headers=key)
    assert r.status_code == 200 and r.json()["inventory_moved"] is True
    assert [d["name"] for d in client.get("/api/device-books").json()["devices"]] == ["Kindle"]

    # The new name already has a list (the device synced first): it wins.
    _put(client, key, "Old", [a.id])
    _put(client, key, "New", [b.id])
    client.post("/api/tome-sync/stats/rename-device",
                json={"from_device": "Old", "to_device": "New"}, headers=key)
    devices = {d["name"]: d["book_ids"] for d in client.get("/api/device-books").json()["devices"]}
    assert "Old" not in devices and devices["New"] == [b.id]


def test_deleting_a_book_drops_it_from_inventories(client, db, make_book):
    a, b = make_book(title="A"), make_book(title="B")
    db.commit()
    _put(client, _key(client), "Kindle", [a.id, b.id])
    db.delete(b)
    db.commit()
    assert db.query(DeviceBook).count() == 1


def test_shelf_with_device_filter_on_the_plugin(client, db, admin_user, make_book):
    user, _ = admin_user
    a, b = make_book(title="A"), make_book(title="B")
    sf = SavedFilter(name="Not on Kindle", owner_id=user.id,
                     params=json.dumps({"not_on_device": "Kindle"}))
    db.add(sf)
    db.commit()
    key = _key(client)
    _put(client, key, "Kindle", [a.id])
    r = client.get("/api/tome-sync/shelf-books", params={"shelf_id": sf.id}, headers=key).json()
    assert [x["title"] for x in r["books"]] == ["B"]
    assert r["unsupported_filters"] == []


def test_deleting_the_user_drops_their_inventories(client, db, make_book):
    a = make_book(title="A")
    db.commit()
    member, token = _member(db)
    _put(client, _key(client, token), "Kindle", [a.id])
    assert db.query(DeviceInventory).count() == 1
    r = client.delete(f"/api/users/{member.id}")
    assert r.status_code in (200, 204), r.text
    db.expire_all()
    assert db.query(DeviceInventory).count() == 0
    assert db.query(DeviceBook).count() == 0


# ── Plugin side (generated Lua) ──────────────────────────────────────────────
# Tripwires on the generated impl; the emulator run covers behaviour.

def _impl() -> str:
    from backend.api.tome_sync import _main_impl_lua
    return _main_impl_lua("http://localhost:8080", "tk_test", "tester")


def _body(lua: str, name: str) -> str:
    import re
    m = re.search(rf"function TomeSync:{name}\(.*?\nend\n", lua, re.S)
    assert m, name
    return m.group(0)


def test_plugin_sends_ids_only_and_skips_missing_files():
    body = _body(_impl(), "_deviceBookIds")
    assert 'lfs.attributes(path, "mode") == "file"' in body
    push = _body(_impl(), "_pushDeviceBooks")
    assert '"/tome-sync/device-books"' in push
    assert "device = deviceName(), book_ids = ids" in push
    # Paths never leave the device.
    assert "path" not in push


def test_plugin_pushes_on_book_map_changes_and_lifecycle():
    lua = _impl()
    assert 'if key == "tomesync_book_map" then self:_scheduleDeviceBooks() end' in _body(lua, "_saveState")
    assert "self:_scheduleDeviceBooks()" in _body(lua, "onResume")
    assert "self:_scheduleDeviceBooks()" in _body(lua, "onNetworkConnected")
    assert "self:_pushDeviceBooks(true)" in lua  # Sync now forces a send
    assert "inventory_launched = true" in lua
