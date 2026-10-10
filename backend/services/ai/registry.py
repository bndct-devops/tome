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
    kind: str  # "metadata" (proposes metadata changes, never writes them)


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
    )
}


def get_feature(key: str) -> Feature | None:
    return FEATURES.get(key)
