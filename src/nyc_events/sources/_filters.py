"""Shared kid-relevance filter helpers.

Each source keeps its own inclusion *strategy* (allowlist / inclusive +
blocklist / category allowlist) and its own venue-specific extras. This module
holds only the cross-source pieces that had drifted apart between six
hand-maintained copies:

- ``normalize()`` lowercases and collapses runs of hyphens/whitespace, so each
  keyword list can hold a single spelling: ``"adults only"`` then also matches
  ``"adults-only"`` and ``"adults  only"``. Match the shared lists against
  ``normalize(text)`` (``contains_any`` does this for you), never raw text.
- ``ADULT_BLOCKLIST`` is the canonical adult-content signal set every editorial
  source shares. ``MEMBERS_ONLY`` is the separate "not bookable by the public"
  signal (usually checked against the title only).

Per-source extras stay local to each source (e.g. ``gala``/``qc ny`` for
Governors Island, the ``Nightlife`` category and ``late night`` for Industry
City, the NYCRUNS race regex). Spelling-variant drift was the bug; the *scope*
each source applies these in (title vs. title+body) is intentionally per-source.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_NORM_RX = re.compile(r"[-\s]+")


def normalize(text: str | None) -> str:
    """Lowercase and collapse runs of hyphens/whitespace into single spaces."""
    if not text:
        return ""
    return _NORM_RX.sub(" ", text.lower()).strip()


def contains_any(text: str | None, keywords: Iterable[str]) -> bool:
    """True if ``normalize(text)`` contains any of ``keywords``.

    Keywords must already be in normalized spelling (single spaces, lowercase).
    """
    haystack = normalize(text)
    return any(kw in haystack for kw in keywords)


# Canonical adult-content signals shared by the editorial sources. Spellings are
# in normalized form — always match via ``normalize``/``contains_any``.
# Note: ``"adults only"`` is a substring of ``"for adults only"``, so the latter
# needs no separate entry.
#
# ``ADULT_BLOCKLIST`` terms are strong enough to drop on a match anywhere (title
# or body). ``ADULT_TITLE_BLOCKLIST`` terms are checked against the **title
# only** — a family festival whose body merely mentions an adjacent "drag show"
# shouldn't be dropped, so the title is the reliable signal for these.
ADULT_BLOCKLIST: tuple[str, ...] = (
    "21+",
    "18+",
    "adults only",
    "adult only",
    "no children",
    "burlesque",
)

# Adult signals checked against the title only (see note above).
ADULT_TITLE_BLOCKLIST: tuple[str, ...] = (
    "drag show",
    "drag brunch",
)

# Members-only events aren't bookable by the public — a distinct signal from
# adult content. Typically checked against the title only.
MEMBERS_ONLY: tuple[str, ...] = ("members only",)
