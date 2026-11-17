"""Shared book filter expressions for the library and saved shelves."""
from sqlalchemy import false
from sqlalchemy.orm import Query

from backend.models.book import Book


def apply_word_count_bounds(
    query: Query[Book],
    min_words: int | str | None = None,
    max_words: int | str | None = None,
) -> Query[Book]:
    """Apply inclusive bounds, excluding unknown counts and invalid shelf values."""
    if min_words in (None, "") and max_words in (None, ""):
        return query
    query = query.filter(Book.word_count > 0)
    for value, minimum in ((min_words, True), (max_words, False)):
        if value in (None, ""):
            continue
        try:
            bound = int(value)
            if bound < 0:
                raise ValueError("Negative word count")
        except (TypeError, ValueError, OverflowError):
            return query.filter(false())
        query = query.filter(Book.word_count >= bound if minimum else Book.word_count <= bound)
    return query
