"""Ask about what you have read (feature ``ask``): the window, the text
shortlist, the pick call, the answer call's blocks, the quote check and the
web + plugin endpoints. FakeProvider only; books and names are made up."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from backend.models.ai_usage import AIUsage
from backend.models.library import Library
from backend.services import ai
from backend.services.ai import ask, companion, digest, settings as ai_settings
from tests.ai_fake import FakeProvider
from tests.test_ai_companion import _all_text, _mid, _position, plugin  # noqa: F401  (plugin is a fixture)
from tests.test_ai_digest import (
    _epub_book, _headers, _make_user, _read, _store_digest, chapter_blocks,
)

ASK_CHAPTERS = [
    ("Arrival", "Mara Vell arrived at the inn at the edge of the marsh. "
                "The landlord gave her the room above the stable. " * 4),
    ("The Key", "Tobin Ash hid the silver key under the loose floorboard in the chapel. "
                "Nobody saw him do it but the crows. " * 4),
    ("Market", "The market filled the square with carts of turnips and wool. "
               "Mara Vell bought bread and a lantern. " * 4),
    ("Storm", "A storm came off the sea and broke the mill wheel. "
              "Everyone sheltered in the long barn until morning. " * 4),
    ("Night", "At night the inn was quiet and the fire burned low. "
              "Mara Vell listened to the rain on the shutters. " * 4),
    ("After", "Wexley found the silver key and opened the crypt beneath the chapel. "
              "Tobin Ash was gone by dawn. " * 4),
]

QUESTION = "Where did Tobin hide the silver key?"


def _is_pick(schema) -> bool:
    return schema is not None and "chapters" in schema.get("properties", {})


def responder(picks=None, answer=None):
    """Pick call: ``picks(book_ids)`` refs; answer call: ``answer``."""
    def respond(feature, user_content, schema):
        assert feature == "ask"
        if _is_pick(schema):
            return {"chapters": picks() if callable(picks) else (picks or [])}
        return answer or {"found": False, "answer": "", "quotes": []}
    return respond


def _ask_book(db, make_book, tmp_path, user, title="Ask Book", chapter=4, frac=0.6, digests=True):
    book = _epub_book(db, make_book, tmp_path, title, chapters=ASK_CHAPTERS)
    if digests:
        for i in range(len(ASK_CHAPTERS)):
            _store_digest(db, book, i, [])
    _position(db, user, book, _mid(book, db, chapter, frac))
    db.commit()
    return book


def _calls(fake, pick: bool) -> list[dict]:
    return [c for c in fake.calls if _is_pick(c["schema"]) == pick]


# ── window + shortlist ───────────────────────────────────────────────────────

def test_window_never_reaches_the_position_or_an_unread_volume(db, make_book, tmp_path):
    user = _make_user(db, "win", "member")
    v1 = _epub_book(db, make_book, tmp_path, "Marsh One", series="Marsh", index=1, chapters=ASK_CHAPTERS)
    v2 = _epub_book(db, make_book, tmp_path, "Marsh Two", series="Marsh", index=2, chapters=ASK_CHAPTERS)
    v3 = _epub_book(db, make_book, tmp_path, "Marsh Three", series="Marsh", index=3, chapters=ASK_CHAPTERS)
    _read(db, user, v1)
    window = companion.pct_window(db, v3, _mid(v3, db, 2, 0.6))
    assert window.chapter_idx == 2
    scope = digest.user_scope(db, user, v3)
    chapters = ask.window_chapters(db, scope, v3, window)
    refs = [wc.ref for wc in chapters]
    assert refs == [(v1.id, i) for i in range(6)] + [(v3.id, 0), (v3.id, 1), (v3.id, 2)]
    assert not any(b == v2.id for b, _ in refs)
    current = chapters[-1]
    assert current.current and "Mark2end" not in current.text
    ask.score_chapters(chapters, ask.content_words(QUESTION))
    shortlist = ask.ranked(chapters)[:ask.SHORTLIST]
    assert all(wc.ref not in {(v3.id, 3), (v3.id, 4), (v3.id, 5)} for wc in shortlist)
    assert all(wc.book.id != v2.id for wc in shortlist)


def test_text_score_picks_the_chapter_holding_the_question_words(db, make_book, tmp_path):
    user = _make_user(db, "score", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Score Book")
    assert ask.content_words(QUESTION) == ["tobin", "hide", "silver", "key"]
    assert ask.content_words("What did Mara's lantern do?") == ["mara", "lantern"]
    window = companion.web_window(db, user, book, None)
    chapters = ask.window_chapters(db, digest.user_scope(db, user, book), book, window)
    ask.score_chapters(chapters, ask.content_words(QUESTION))
    top = ask.ranked(chapters)[0]
    assert top.ref == (book.id, 1) and top.score == 3      # tobin, silver, key ("hid" is not "hide")
    # Ties go to the chapter nearer the position.
    zero = [wc for wc in ask.ranked(chapters) if wc.score == 0]
    assert [wc.chapter.idx for wc in zero] == sorted((wc.chapter.idx for wc in zero), reverse=True)


# ── the pick call + the answer call ──────────────────────────────────────────

def test_pick_refs_outside_the_window_are_dropped_and_blocks_hold_only_the_choice(
        client, db, make_book, tmp_path):
    user = _make_user(db, "pick", "member")
    other = _ask_book(db, make_book, tmp_path, user, "Other Book")
    book = _ask_book(db, make_book, tmp_path, user, "Pick Book")
    picks = [{"book_id": book.id, "chapter_idx": 5},          # past the position
             {"book_id": book.id, "chapter_idx": 4},          # the position chapter (cut)
             {"book_id": other.id, "chapter_idx": 0},         # another book
             {"book_id": book.id, "chapter_idx": 99},         # no such chapter
             {"book_id": book.id, "chapter_idx": 0}]
    fake = FakeProvider(responses=responder(picks))
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{book.id}/ask", json={"question": QUESTION}, headers=_headers(user))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["found"] is False and out["answer"] == ask.NOT_FOUND_MESSAGE
    assert out["chapter_idx"] == 4 and out["chapter_title"] == "Night"

    [pick] = _calls(fake, True)
    sent_pick = _all_text(pick)
    assert "Mark4end" not in sent_pick and "Wexley" not in sent_pick
    assert f"Summary {book.id}:4" not in sent_pick and f"Summary {book.id}:5" not in sent_pick
    assert f"Summary {book.id}:3" in sent_pick

    [answer] = _calls(fake, False)
    blocks = chapter_blocks(answer["user_content"])
    sent_refs = {(r["book_id"], r["chapter_idx"]) for r in out["chapters_read"]}
    assert all(b == book.id for b, _ in sent_refs)
    assert (book.id, 5) not in sent_refs and (book.id, 0) in sent_refs and (book.id, 4) in sent_refs
    assert (book.id, 1) in sent_refs                         # top text match
    assert sorted(n - 1 for n, _ in blocks) == sorted(i for _, i in sent_refs)
    joined = _all_text(answer)
    assert "Mark4end" not in joined and "Mark5end" not in joined and "Wexley" not in joined
    assert "Mark0end" in joined and "Mark1end" in joined
    # Chapter text first, never marked for caching, the question after it.
    chapter_part = [b for b in answer["user_content"] if b["text"].startswith("=== [c")]
    assert all("cache_control" not in b for b in answer["user_content"])
    assert QUESTION in answer["user_content"][-1]["text"]
    assert answer["user_content"][-1] is not chapter_part[-1]


def test_fewer_than_four_chapters_skips_the_pick_call(db, make_book, tmp_path):
    user = _make_user(db, "small", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Small Book", chapter=2, frac=0.6)
    fake = FakeProvider(responses=responder())
    ai.set_provider_override(fake)
    window = companion.web_window(db, user, book, None)
    out = ask.ask(db, user, book, window, QUESTION)
    assert _calls(fake, True) == []
    assert [c["chapter_idx"] for c in out["chapters_read"]] == [0, 1, 2]
    assert "Mark2end" not in _all_text(fake.calls[0])


def test_quotes_are_checked_against_the_chapter_they_name(db, make_book, tmp_path):
    user = _make_user(db, "quotes", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Quote Book")
    window = companion.web_window(db, user, book, None)
    good = {"book_id": book.id, "chapter_idx": 1,
            "text": "Tobin Ash hid the silver key under the loose floorboard"}
    wrong_chapter = {"book_id": book.id, "chapter_idx": 0,
                     "text": "hid the silver key under the loose floorboard"}
    invented = {"book_id": book.id, "chapter_idx": 1, "text": "Tobin buried the key in the garden"}
    future = {"book_id": book.id, "chapter_idx": 5, "text": "Wexley found the silver key"}
    fake = FakeProvider(responses=responder(
        lambda: [{"book_id": book.id, "chapter_idx": 1}],
        {"found": True, "answer": "He hid it under a floorboard in the chapel — quietly.",
         "quotes": [good, wrong_chapter, invented, future]}))
    ai.set_provider_override(fake)
    out = ask.ask(db, user, book, window, QUESTION)
    assert out["found"] is True
    assert out["answer"] == "He hid it under a floorboard in the chapel - quietly."
    assert [q["text"] for q in out["quotes"]] == [good["text"]]
    q = out["quotes"][0]
    assert q["chapter_idx"] == 1 and q["chapter_title"] == "The Key" and q["book_id"] == book.id

    # No quote survives: found flips to false with the fixed wording.
    fake.responses = responder(
        lambda: [{"book_id": book.id, "chapter_idx": 1}],
        {"found": True, "answer": "He buried it.", "quotes": [invented, wrong_chapter, future]})
    out = ask.ask(db, user, book, window, QUESTION)
    assert out["found"] is False and out["quotes"] == []
    assert out["answer"] == ask.NOT_FOUND_MESSAGE == "Nothing in the chapters you have read answers that."


def test_long_answer_is_capped(db, make_book, tmp_path):
    user = _make_user(db, "long", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Long Book")
    window = companion.web_window(db, user, book, None)
    fake = FakeProvider(responses=responder(
        [], {"found": True, "answer": "word " * 600,
             "quotes": [{"book_id": book.id, "chapter_idx": 1, "text": "Tobin Ash hid the silver key"}]}))
    ai.set_provider_override(fake)
    out = ask.ask(db, user, book, window, QUESTION)
    assert out["found"] is True and len(out["answer"]) <= ask.MAX_ANSWER_CHARS + 3


# ── endpoints ────────────────────────────────────────────────────────────────

def test_web_endpoint_gates(client: TestClient, db: Session, admin_user, make_book, tmp_path):
    member = _make_user(db, "asker", "member")
    keyless = _make_user(db, "askless", "member", key=False)
    guest = _make_user(db, "askguest", "guest", key=False)
    book = _ask_book(db, make_book, tmp_path, member, "Gate Book")
    hidden = _ask_book(db, make_book, tmp_path, member, "Gate Hidden")
    _position(db, keyless, book, _mid(book, db, 4, 0.6))
    lib = Library(name="Private", is_public=False, owner_id=admin_user[0].id)
    lib.books.append(hidden)
    db.add(lib)
    db.commit()
    fake = FakeProvider(responses=responder())
    ai.set_provider_override(fake)
    url = f"/api/ai/books/{book.id}/ask"

    assert client.post(url, json={"question": ""}, headers=_headers(member)).status_code == 422
    assert client.post(url, json={"question": "   "}, headers=_headers(member)).status_code == 422
    assert client.post(url, json={"question": "x" * 301}, headers=_headers(member)).status_code == 422
    assert client.post(url, json={"question": QUESTION}, headers=_headers(guest)).status_code == 403
    assert client.post(f"/api/ai/books/{hidden.id}/ask", json={"question": QUESTION},
                       headers=_headers(member)).status_code == 404
    assert client.post(url, json={"question": QUESTION}, headers=_headers(keyless)).status_code == 409

    ai_settings.set_feature_enabled(db, "ask", False)
    db.commit()
    assert client.post(url, json={"question": QUESTION}, headers=_headers(member)).status_code == 404
    ai_settings.set_feature_enabled(db, "ask", True)
    ai_settings.set_feature_enabled(db, "reading_digest", False)
    db.commit()
    assert client.post(url, json={"question": QUESTION}, headers=_headers(member)).status_code == 404
    ai_settings.set_feature_enabled(db, "reading_digest", True)
    # Ask has its own switch: Recall off does not hide it.
    ai_settings.set_feature_enabled(db, "recall", False)
    db.commit()
    assert fake.calls == []
    assert client.post(url, json={"question": QUESTION}, headers=_headers(member)).status_code == 200


def test_plugin_route_by_pct_two_usage_rows(db, make_book, tmp_path, plugin):
    c, key_for = plugin
    user = _make_user(db, "kask", "member")
    guest = _make_user(db, "kaskguest", "guest", key=False)
    book = _ask_book(db, make_book, tmp_path, user, "Plugin Ask")
    h, hg = key_for(user), key_for(guest)
    db.commit()
    fake = FakeProvider(responses=responder(
        lambda: [{"book_id": book.id, "chapter_idx": 1}],
        {"found": True, "answer": "Under a floorboard in the chapel.",
         "quotes": [{"book_id": book.id, "chapter_idx": 1, "text": "under the loose floorboard in the chapel"}]}))
    ai.set_provider_override(fake)
    url = f"/api/tome-sync/ai/books/{book.id}/ask"
    pct = _mid(book, db, 3, 0.6)

    assert c.post(url, json={"question": QUESTION, "pct": pct}, headers=hg).status_code == 403
    assert c.post(url, json={"question": "", "pct": pct}, headers=h).status_code == 422
    r = c.post(url, json={"question": QUESTION, "pct": pct}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["found"] is True and out["chapter_idx"] == 3
    assert out["quotes"][0]["chapter_idx"] == 1
    assert all(ch["chapter_idx"] <= 3 for ch in out["chapters_read"])
    sent = "\n".join(_all_text(call) for call in fake.calls)
    assert "Mark3end" not in sent and "Mark4end" not in sent
    rows = db.query(AIUsage).filter_by(user_id=user.id).all()
    assert len(rows) == 2 and {r.feature for r in rows} == {"ask"}
    assert all(r.book_id == book.id for r in rows)


@pytest.mark.parametrize("question", ["the", "?!"])
def test_question_without_content_words_still_reads_the_nearest_chapters(db, make_book, tmp_path, question):
    user = _make_user(db, f"nowords{len(question)}", "member")
    book = _ask_book(db, make_book, tmp_path, user, f"No Words {len(question)}")
    window = companion.web_window(db, user, book, None)
    fake = FakeProvider(responses=responder())
    ai.set_provider_override(fake)
    if not any(ch.isalnum() for ch in question):
        with pytest.raises(ask.BadQuestion):
            ask.ask(db, user, book, window, question)
        return
    out = ask.ask(db, user, book, window, question)
    assert out["found"] is False
    assert (book.id, 4) in {(c["book_id"], c["chapter_idx"]) for c in out["chapters_read"]}


# ── run 2d review ────────────────────────────────────────────────────────────

def test_limits_and_prompts():
    assert ask.ASK_MAX_WORDS == 40_000 and ask.MAX_CHAPTERS == 8
    assert "up to 8 chapters" in " ".join(ask.PICK_SYSTEM_PROMPT.split())
    flat = " ".join(ask.ANSWER_SYSTEM_PROMPT.split())
    assert '"no one", "nothing happened"' in flat and "found true" in flat


def test_a_failed_pick_call_falls_back_to_the_text_shortlist(db, make_book, tmp_path):
    from backend.services.ai.provider import AIProviderError

    user = _make_user(db, "pickfail", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Pick Fail")
    window = companion.web_window(db, user, book, None)

    def respond(feature, user_content, schema):
        if _is_pick(schema):
            raise AIProviderError("overloaded")
        return {"found": True, "answer": "Under a floorboard.",
                "quotes": [{"book_id": book.id, "chapter_idx": 1,
                            "text": "under the loose floorboard in the chapel"}]}

    fake = FakeProvider(responses=respond)
    ai.set_provider_override(fake)
    out = ask.ask(db, user, book, window, QUESTION)
    assert out["found"] is True and out["quotes"][0]["chapter_idx"] == 1
    assert len(_calls(fake, True)) == 1 and len(_calls(fake, False)) == 1
    assert (book.id, 1) in {(c["book_id"], c["chapter_idx"]) for c in out["chapters_read"]}
    assert len(out["chapters_read"]) <= ask.MAX_CHAPTERS


def test_a_budget_error_on_the_pick_call_is_not_swallowed(db, make_book, tmp_path, monkeypatch):
    from backend.services.ai.provider import AIBudgetExceeded

    user = _make_user(db, "pickbudget", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Pick Budget")
    window = companion.web_window(db, user, book, None)
    fake = FakeProvider(responses=responder())
    ai.set_provider_override(fake)

    def over(*_a, **_k):
        raise AIBudgetExceeded("over")

    monkeypatch.setattr(ask, "_pick", over)
    with pytest.raises(AIBudgetExceeded):
        ask.ask(db, user, book, window, QUESTION)
    assert fake.calls == []


def test_ask_calls_carry_max_tokens_guesses(db, make_book, tmp_path, monkeypatch):
    from backend.services.ai import estimate

    user = _make_user(db, "askguess", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Ask Guess")
    window = companion.web_window(db, user, book, None)
    seen: list[dict] = []
    real = ai.run_feature

    def spy(db_, user_, feature, **kw):
        seen.append(kw)
        return real(db_, user_, feature, **kw)

    monkeypatch.setattr(ai, "run_feature", spy)
    ai.set_provider_override(FakeProvider(responses=responder()))
    ask.ask(db, user, book, window, QUESTION)
    pick, answer = seen
    model = ai_settings.effective_model(db, "ask")
    assert pick["cost_guess"] == pytest.approx(
        estimate.call_cost_guess("ask", model, sum(len(b["text"].split()) for b in pick["user_content"]),
                                 max_tokens=ask.PICK_MAX_TOKENS, cached_words=0))
    assert answer["cost_guess"] == pytest.approx(
        estimate.call_cost_guess("ask", model, sum(len(b["text"].split()) for b in answer["user_content"]),
                                 max_tokens=ask.ANSWER_MAX_TOKENS, cached_words=0))
    assert ask.cost_hint_usd(db) == round(estimate.call_cost_guess(
        "ask", model, ask.ASK_MAX_WORDS, max_tokens=ask.ANSWER_MAX_TOKENS, cached_words=0), 2)


def test_ask_in_flight_is_a_409(client, db, make_book, tmp_path, plugin):
    c, key_for = plugin
    user = _make_user(db, "askbusy", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Ask Busy")
    hp = key_for(user)
    db.commit()
    fake = FakeProvider(responses=responder())
    ai.set_provider_override(fake)
    with companion.in_flight("ask", user.id, book.id, "busy"):
        r = client.post(f"/api/ai/books/{book.id}/ask", json={"question": QUESTION}, headers=_headers(user))
        assert r.status_code == 409 and r.json()["reason"] == "already_running"
        r = c.post(f"/api/tome-sync/ai/books/{book.id}/ask",
                   json={"question": QUESTION, "pct": _mid(book, db, 4, 0.6)}, headers=hp)
        assert r.status_code == 409
    assert fake.calls == []
    # Released after a call too, also when it fails.
    assert client.post(f"/api/ai/books/{book.id}/ask", json={"question": QUESTION},
                       headers=_headers(user)).status_code == 200
    assert client.post(f"/api/ai/books/{book.id}/ask", json={"question": QUESTION},
                       headers=_headers(user)).status_code == 200


def test_bad_ask_bodies_are_422_with_a_plain_message(client, db, make_book, tmp_path, plugin):
    c, key_for = plugin
    user = _make_user(db, "askbody", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Ask Body")
    hp = key_for(user)
    db.commit()
    fake = FakeProvider(responses=responder())
    ai.set_provider_override(fake)
    web = f"/api/ai/books/{book.id}/ask"
    plug = f"/api/tome-sync/ai/books/{book.id}/ask"
    for kw in ({}, {"json": {}}, {"json": {"question": 5}}, {"json": ["x"]},
               {"json": {"question": "x" * 301}}, {"content": b"not json",
                                                    "headers_extra": {"Content-Type": "application/json"}}):
        extra = kw.pop("headers_extra", {})
        r = client.post(web, headers={**_headers(user), **extra}, **kw)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), (kw, r.text)
    for body in ({}, {"question": QUESTION, "pct": "half"}, {"question": QUESTION, "pct": 2},
                 {"question": None, "pct": 0.5}, {"question": QUESTION, "pct": True},
                 {"question": QUESTION, "pct": 0.5, "progress": 7}):
        r = c.post(plug, json=body, headers=hp)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str), (body, r.text)
    assert fake.calls == []
    # Without pct the plugin asks at the stored position.
    r = c.post(plug, json={"question": QUESTION}, headers=hp)
    assert r.status_code == 200 and r.json()["chapter_idx"] == 4


def test_ask_on_a_finished_book_reads_to_the_end(client, db, make_book, tmp_path, plugin):
    """Marked read with the device at 100%: the window is the whole book, no
    current chapter, the last chapter is read in full."""
    c, key_for = plugin
    user = _make_user(db, "askdone", "member")
    book = _ask_book(db, make_book, tmp_path, user, "Ask Done", chapter=5, frac=0.99)
    _position(db, user, book, 1.0)
    _read(db, user, book)
    hp = key_for(user)
    db.commit()
    from backend.services.ai import digest_job
    digest_job.reset()
    fake = FakeProvider(responses=responder(
        lambda: [{"book_id": book.id, "chapter_idx": 5}],
        {"found": True, "answer": "Wexley opened the crypt.",
         "quotes": [{"book_id": book.id, "chapter_idx": 5, "text": "Wexley found the silver key"}]}))
    ai.set_provider_override(fake)
    r = client.post(f"/api/ai/books/{book.id}/ask", json={"question": "Who opened the crypt?"},
                    headers=_headers(user))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["found"] is True and out["chapter_idx"] == len(ASK_CHAPTERS)
    assert out["chapter_title"] is None and out["quotes"][0]["chapter_idx"] == 5
    assert "Mark5end" in _all_text(_calls(fake, False)[0])           # the last chapter in full
    r = c.post(f"/api/tome-sync/ai/books/{book.id}/ask",
               json={"question": "Who opened the crypt?", "pct": 1.0}, headers=hp)
    assert r.status_code == 200 and r.json()["chapter_idx"] == len(ASK_CHAPTERS)
