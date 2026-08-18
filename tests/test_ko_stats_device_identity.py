"""Device identity for imported KOReader history (issue #181, plugin build 40).

Before build 40 the plugin reported the literal "KOReader" for every device, so
all of a user's devices shared one reading-history watermark. Build 40 reports
Device.model (or a user-set name) and migrates its history over; the server
dedups page-stats regardless of label so a re-send under a new name is a no-op.
"""
from backend.api.tome_sync import TOMESYNC_PLUGIN_BUILD, _main_impl_lua
from backend.models.ko_stats import PageStat, StatsImport
from backend.services.ko_stats_import import import_batch, rename_device

BOOKS = [{"ko_id": 1, "md5": "e" * 32, "title": "Identity Test Book",
          "authors": "A. Author", "pages": 100, "total_read_pages": 10}]


def _rows(start, n, page0=1):
    return [{"ko_id": 1, "page": page0 + i, "start_time": start + i * 60, "duration": 30,
             "total_pages": 100} for i in range(n)]


def test_resend_under_new_device_label_is_a_noop(db, admin_user, make_book):
    user, _ = admin_user
    make_book(title="Identity Test Book", author="A. Author")
    first = import_batch(db, user, device="KOReader", books=BOOKS, page_stats=_rows(1_700_000_000, 5))
    assert first["page_rows_imported"] == 5
    # same reading, now labelled with the real device name (watermark 0 → full re-send)
    again = import_batch(db, user, device="KindlePaperWhite4", books=BOOKS,
                         page_stats=_rows(1_700_000_000, 5) + _rows(1_700_001_000, 2, page0=6))
    assert again["page_rows_imported"] == 2          # only the genuinely new rows
    assert again["page_rows_skipped"] == 5
    assert db.query(PageStat).filter_by(user_id=user.id).count() == 7


def test_watermarks_are_per_device(db, admin_user, make_book):
    user, _ = admin_user
    make_book(title="Identity Test Book", author="A. Author")
    import_batch(db, user, device="Kindle", books=BOOKS, page_stats=_rows(1_700_000_000, 3))
    import_batch(db, user, device="Phone", books=BOOKS, page_stats=_rows(1_600_000_000, 3, page0=50))
    wms = {w.device: w.last_start_time_synced for w in db.query(StatsImport).filter_by(user_id=user.id)}
    assert wms == {"Kindle": 1_700_000_120, "Phone": 1_600_000_120}


def test_rename_moves_history_and_watermark(db, admin_user, make_book):
    """The build-40 adoption: legacy "KOReader" rows + watermark move to the
    device's real name, so the next sync resumes from where it was."""
    user, _ = admin_user
    make_book(title="Identity Test Book", author="A. Author")
    import_batch(db, user, device="KOReader", books=BOOKS, page_stats=_rows(1_700_000_000, 5))

    res = rename_device(db, user, from_device="KOReader", to_device="KindlePaperWhite4")
    assert res == {"rows_relabelled": 5, "rows_deduplicated": 0, "watermark_moved": True}
    assert db.query(PageStat).filter_by(user_id=user.id, device="KOReader").count() == 0
    assert db.query(PageStat).filter_by(user_id=user.id, device="KindlePaperWhite4").count() == 5
    wms = {w.device: w.last_start_time_synced for w in db.query(StatsImport).filter_by(user_id=user.id)}
    assert wms == {"KindlePaperWhite4": 1_700_000_240}

    # idempotent
    assert rename_device(db, user, from_device="KOReader", to_device="KindlePaperWhite4") == {
        "rows_relabelled": 0, "rows_deduplicated": 0, "watermark_moved": False}


def test_rename_dedups_rows_already_synced_under_new_name(db, admin_user, make_book):
    """A sync under the new name that ran before the rename landed: the
    old-label copies of the same rows are dropped, not relabelled into a
    constraint violation; watermarks merge to the max."""
    user, _ = admin_user
    make_book(title="Identity Test Book", author="A. Author")
    import_batch(db, user, device="KOReader", books=BOOKS, page_stats=_rows(1_700_000_000, 5))
    # new name already holds rows 3-5 (same reading) plus one newer row
    db.add_all([
        PageStat(user_id=user.id, book_id=db.query(PageStat.book_id).first()[0], page=p,
                 total_pages=100, start_time=1_700_000_000 + (p - 1) * 60, duration_seconds=30,
                 device="Kindle")
        for p in (3, 4, 5, 6)
    ])
    db.add(StatsImport(user_id=user.id, device="Kindle", last_start_time_synced=1_700_000_300,
                       rows_imported=4))
    db.flush()

    res = rename_device(db, user, from_device="KOReader", to_device="Kindle")
    assert res == {"rows_relabelled": 2, "rows_deduplicated": 3, "watermark_moved": True}
    assert db.query(PageStat).filter_by(user_id=user.id).count() == 6
    assert db.query(PageStat).filter_by(user_id=user.id, device="KOReader").count() == 0
    wms = {w.device: (w.last_start_time_synced, w.rows_imported)
           for w in db.query(StatsImport).filter_by(user_id=user.id)}
    assert wms == {"Kindle": (1_700_000_300, 9)}


def test_rename_endpoint_requires_plugin_key(client, db, admin_user, make_book):
    user, _ = admin_user
    from backend.models.tome_sync import ApiKey
    plain = ApiKey.generate()
    db.add(ApiKey(user_id=user.id, key_hash=ApiKey.hash_key(plain), key_prefix=plain[:11], label="t"))
    db.flush()
    make_book(title="Identity Test Book", author="A. Author")
    import_batch(db, user, device="KOReader", books=BOOKS, page_stats=_rows(1_700_000_000, 2))
    r = client.post("/api/tome-sync/stats/rename-device",
                    json={"from_device": "KOReader", "to_device": "Kobo_clara"},
                    headers={"Authorization": f"Bearer {plain}"})
    assert r.status_code == 200, r.text
    assert r.json()["rows_relabelled"] == 2
    r = client.get("/api/tome-sync/stats/watermark?device=Kobo_clara",
                   headers={"Authorization": f"Bearer {plain}"})
    assert r.json()["last_start_time_synced"] == 1_700_000_060


# ── plugin source shape ────────────────────────────────────────────────────────

def test_plugin_reports_a_real_device_name():
    impl = _main_impl_lua("http://localhost:8080", "tk_test", "tester")
    assert TOMESYNC_PLUGIN_BUILD >= 40
    assert "getFriendlyDeviceName" not in impl          # never existed in KOReader
    assert 'G_reader_settings:readSetting("tomesync_device_name")' in impl
    assert "Device.model" in impl
    # adoption runs before the watermark is fetched
    i_adopt = impl.index("if not self:_adoptDeviceName() then")
    i_wm = impl.index('apiRequest("GET", "/tome-sync/stats/watermark?device="')
    assert i_adopt < i_wm          # ...and an unconfirmed move postpones the sync
    assert "reading-history sync postponed" in impl
    assert '"/tome-sync/stats/rename-device"' in impl
    assert 'return "Device name: " .. deviceName()' in impl
