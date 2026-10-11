"""The AI features Tome knows about, with their shipped defaults.

One row per feature. Labels and descriptions are English (the frontend shows
its own translated copy keyed by ``key``; these are the fallback and what the
API returns). ``default_model`` ships with the release; admins can override it
per feature in Settings (instance setting ``ai.feature.<key>.model``).
"""
from __future__ import annotations

from dataclasses import dataclass

PROVIDER = "anthropic"
PROVIDER_LABEL = "Anthropic"

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"

# Model ids an admin may pick in the per-feature override.
MODELS: tuple[str, ...] = (OPUS, SONNET, HAIKU)


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    description: str
    default_model: str
    effort: str
    # "metadata": proposes metadata changes, never writes them.
    # "reading": the reading companion (digests, recap, Recall); text up to
    # the reader's position only. Nothing switches on it yet.
    kind: str


FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature(
            key="bindery_identify",
            label="Identify in the Bindery",
            description="Pre-fills title, author, series, volume, type and tags for new files "
                        "from the filename, embedded metadata and the first pages.",
            default_model=OPUS,
            effort="high",
            kind="metadata",
        ),
        Feature(
            key="fix_book",
            label="Fix this book",
            description="Picks the right metadata match for one book and proposes a diff to confirm.",
            default_model=OPUS,
            effort="high",
            kind="metadata",
        ),
        Feature(
            key="series_cleanup",
            label="Clean up this series",
            description="Proposes one diff for a series: name, volume titles and numbers, status and arcs.",
            default_model=OPUS,
            effort="high",
            kind="metadata",
        ),
        Feature(
            key="reading_digest",
            label="Build the glossary as you read",
            description="Reads each finished chapter once and keeps a short summary and the "
                        "names, places and terms it introduces, for the recap and the glossary.",
            default_model=SONNET,
            # Low: a chapter digest is extraction, not reasoning, and runs
            # hundreds of times per series (medium thinking cost ~5k output
            # tokens a chapter on the first real run).
            effort="low",
            kind="reading",
        ),
        Feature(
            key="recap",
            label="Recap to here",
            description="A few paragraphs on the story so far, from the chapter summaries up to "
                        "your position. Nothing after it.",
            default_model=OPUS,
            effort="high",
            kind="reading",
        ),
        Feature(
            key="recall",
            label="Recall",
            description="One line on a name, place or term, from the glossary or the current "
                        "chapter up to your position.",
            default_model=OPUS,
            effort="medium",
            kind="reading",
        ),
        Feature(
            key="ask",
            label="Ask about what you have read",
            description="Answers a question from the chapters you have read, with a quote for "
                        "every claim. Nothing after your position.",
            default_model=SONNET,
            # One effort per feature: medium for the answer. The chapter-pick
            # call runs at it too (small max_tokens keeps it cheap).
            effort="medium",
            kind="reading",
        ),
    )
}


def get_feature(key: str) -> Feature | None:
    return FEATURES.get(key)
