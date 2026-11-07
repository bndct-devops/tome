"""One rule for Hardcover API tokens, wherever they come from.

Hardcover wants the literal ``Bearer <jwt>`` in the ``authorization`` header
(a bare token answers "Malformed Authorization header"), and its token page
shows the value with the prefix already attached. People paste both forms,
and admins set ``TOME_HARDCOVER_TOKEN`` both ways too. Every request Tome
makes to Hardcover goes through :func:`hardcover_auth`, so either form works.
"""

from __future__ import annotations


def hardcover_auth(token: str) -> str:
    """Return the token as Hardcover wants it in the authorization header:
    stripped, with exactly one ``Bearer `` prefix (case-insensitive on input)."""
    value = (token or "").strip()
    if not value:
        return ""
    if value[:7].lower() == "bearer ":
        value = value[7:].strip()
    return f"Bearer {value}"
