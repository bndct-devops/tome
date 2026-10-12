"""The shared identity gate (backend/services/identity_match.py) used by
auto-import and the AI book fix. Every title here is invented, except the
well-known pairs that show a class of false match."""
import pytest

from backend.services.identity_match import agrees, norm
from backend.services.metadata_fetch import MetadataCandidate


def t(title, author=None, **kw):
    return {"title": title, "author": author, **kw}


@pytest.mark.parametrize("identity,candidate,expected", [
    # volume numbers
    (t("Lanternfall, Vol. 3"), t("Lanternfall, Vol. 1"), False),
    (t("Lanternfall 3"), t("Lanternfall 13"), False),
    (t("Lanternfall, Vol. 3"), t("Lanternfall Volume 3"), True),
    (t("Lanternfall, Vol. 3"), t("Lanternfall", series_index=1.0), False),
    (t("Lanternfall", series_index=2.0), t("Lanternfall", series_index=2.0), True),
    # collections
    (t("Strangers"), t("Dean Koontz Box Set: Strangers, Lightning, Midnight"), False),
    (t("Midnight Omnibus"), t("Midnight Omnibus"), True),
    # containment needs a subtitle separator or most of the words
    (t("Dune", "Frank Herbert"), t("Dune Messiah", "Frank Herbert"), False),
    (t("The Quiet Harbour"), t("The Quiet Harbour: A Novel"), True),
    (t("The Quiet Harbour", "M. Vale"), t("The Quiet Harbour (Harbour Tales, #2)", "M. Vale"), True),
    (t("Ember Crown"), t("Ember Crown - Volume 3"), True),
    # ratio needs a shared word, or must be a near spelling variant
    (t("Ember"), t("Remember Me"), False),
    (t("Harbour"), t("Harboured Secrets"), False),
    (t("The Quiet Harbour"), t("The Quiet Harbor"), True),
    # scripts beyond Latin
    (t("進撃の巨人 3"), t("鬼滅の刃 3"), False),
    (t("Тихая гавань", "Мира Вейл"), t("Тихая гавань", "Мира Вейл"), True),
    # not comparable
    (t(""), t("The Quiet Harbour"), False),
    (t("!!!"), t("!!!"), False),
    (t("1984"), t("1984"), False),
    # authors
    (t("The Quiet Harbour", "M. Vale"), t("The Quiet Harbour", "Vale, M."), True),
    (t("The Quiet Harbour", "M. Vale"), t("The Quiet Harbour", "M. Vale, J. Doe"), True),
    (t("The Quiet Harbour", None), t("The Quiet Harbour", "Someone Else"), True),
    (t("The Quiet Harbour", "M. Vale"), t("The Quiet Harbour", "Oswin Thale"), False),
])
def test_agrees(identity, candidate, expected):
    assert agrees(identity, candidate) is expected


def test_takes_objects():
    cand = MetadataCandidate(source="x", source_id="1", title="Lanternfall, Vol. 1",
                             author="Ida Brook", series_index=1.0)
    assert agrees({"title": "Lanternfall", "author": "Ida Brook", "series_index": 1.0}, cand)
    assert not agrees({"title": "Lanternfall", "author": "Ida Brook", "series_index": 2.0}, cand)


def test_norm_keeps_non_latin_words():
    assert norm("Ébène, Vol. 2!") == "ebene vol 2"
    assert norm("進撃の巨人　3") == "進撃の巨人 3"
    assert norm("Тихая  гавань") == "тихая гавань"
