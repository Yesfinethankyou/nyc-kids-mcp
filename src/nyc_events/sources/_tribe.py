"""Shared machinery for WordPress / The Events Calendar ("Tribe") sources.

Four venues expose the identical plugin REST API: Green-Wood Cemetery,
Prospect Park Alliance, NY Transit Museum, and Industry City. This module
owns everything that is a property of the *plugin*, not the venue:

- ``TribeEventsSource`` — the fetch/pagination loop (`next_rest_url`, page
  cap, politeness delay) and the curl_cffi page fetch with Chrome
  impersonation (several of these sites bot-block plain fetchers).
- ``parse_row`` — the common row skeleton: kid-relevance gate, title, UTC
  dates, per-occurrence external_id, url, excerpt-preferred description with
  truncation, and raw_payload. The per-venue Event construction (venue /
  borough / price / tags) is the ``build_event`` callback.
- ``strip_html`` / ``parse_utc_dt`` / ``parse_cost`` / ``category_names`` —
  the field parsers. ``strip_html`` is the canonical variant using
  ``html.unescape`` (the four hand-maintained copies had drifted: some
  decoded only a fixed handful of entities).

Each source keeps what is a property of the *venue*: its kid-relevance
strategy (keyword allowlist vs category allowlist — shared adult signals
in ``_filters.py``, strategy documented per source module),
tag rules, and venue/borough/price mapping. Sources define a module-level
``_parse_row(row)`` on top of ``parse_row`` and assign it into their class
via ``staticmethod``, so parser tests keep calling it directly with fixture
dicts (no network, no mocking).
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from abc import abstractmethod
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from curl_cffi import requests as cffi_requests

from ..models import Event, Price
from .base import Source

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 nyc-kids-mcp/1.0"
)
DEFAULT_WINDOW_DAYS = 60
DEFAULT_PER_PAGE = 50
PAGE_DELAY_SECONDS = 1.0
DEFAULT_HTTP_TIMEOUT = 30.0

_HTML_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"\s+")


def strip_html(raw: str | None) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    if not raw:
        return ""
    text = _HTML_TAG_RX.sub(" ", raw)
    text = html.unescape(text).replace("\xa0", " ")
    return _WS_RX.sub(" ", text).strip()


def parse_utc_dt(raw: str | None) -> datetime | None:
    """Parse a Tribe UTC naive datetime string ('2026-06-14 14:00:00') into UTC-aware."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def parse_cost(cost: str | None) -> Price:
    """Map a free-text Tribe cost string ("Free", "$30 / $24 members", "") to Price."""
    if not cost:
        return Price.UNKNOWN
    cost_lower = cost.strip().lower()
    if "free" in cost_lower:
        return Price.FREE
    if "$" in cost:
        return Price.PAID
    return Price.UNKNOWN


def category_names(row: dict[str, Any]) -> set[str]:
    """Extract upstream category names from a Tribe row."""
    return {c.get("name", "") for c in (row.get("categories") or [])}


class RowParts(NamedTuple):
    """The source-agnostic fields parse_row extracts from a Tribe row, handed
    to the per-source build_event callback."""

    title: str
    start_dt: datetime
    end_dt: datetime | None
    external_id: str | None
    url: str | None
    description: str | None  # excerpt-preferred, truncated to 2000 chars
    description_text: str  # full stripped description ("" when absent)
    raw_payload: str


def parse_row(
    row: dict[str, Any],
    *,
    source: str,
    is_kid_relevant: Callable[[dict[str, Any]], bool],
    build_event: Callable[[dict[str, Any], RowParts], Event],
) -> Event | None:
    """Common Tribe row skeleton: filter, extract RowParts, delegate Event
    construction to the source's build_event. Returns None when filtered out
    or the row lacks a title / parseable start date.

    The Tribe ``id`` is per-occurrence on all four sites (recurring events get
    a distinct id + dated URL slug per occurrence — verified per source, see
    each module docstring), so ``external_id = str(id)`` needs no
    ``:start.isoformat()`` suffix.
    """
    if not is_kid_relevant(row):
        return None

    title = strip_html(row.get("title"))
    if not title:
        logger.debug("%s: skipping row with no title: id=%r", source, row.get("id"))
        return None

    start_dt = parse_utc_dt(row.get("utc_start_date"))
    if start_dt is None:
        logger.debug("%s: skipping %r — no parseable start date", source, title)
        return None

    excerpt_text = strip_html(row.get("excerpt"))
    description_text = strip_html(row.get("description"))
    description = excerpt_text or description_text or None
    # Trim runaway bodies (and any CSS noise that bleeds through the strip).
    if description and len(description) > 2000:
        description = description[:2000].rsplit(" ", 1)[0] + "…"

    parts = RowParts(
        title=title,
        start_dt=start_dt,
        end_dt=parse_utc_dt(row.get("utc_end_date")),
        external_id=str(row["id"]) if row.get("id") else None,
        url=row.get("url") or None,
        description=description,
        description_text=description_text,
        raw_payload=json.dumps(row, sort_keys=True, default=str),
    )
    return build_event(row, parts)


class TribeEventsSource(Source):
    """Base for Tribe REST API sources.

    Subclasses set ``name``, ``events_url``, ``max_pages``, and assign their
    module-level parser via ``_parse_row = staticmethod(_parse_row)``. All
    four Tribe sources re-fetch their full window each run, so the base opts
    into missing-event detection (``Source.window_days``) by default.
    """

    events_url: str  # subclass class attr
    max_pages: int = 30  # safety cap on pagination; override per source
    default_window_days: int = DEFAULT_WINDOW_DAYS

    def __init__(
        self,
        *,
        events_url: str | None = None,
        window_days: int | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        page_delay: float = PAGE_DELAY_SECONDS,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
        max_pages: int | None = None,
    ):
        self._events_url = events_url if events_url is not None else type(self).events_url
        # window_days is both the fetch-window width and the missing-detection
        # opt-in (see Source.window_days) — one attribute, one value.
        self.window_days = window_days if window_days is not None else self.default_window_days
        self._per_page = per_page
        self._delay = page_delay
        self._timeout = http_timeout
        self._max_pages = max_pages if max_pages is not None else type(self).max_pages

    @staticmethod
    @abstractmethod
    def _parse_row(row: dict[str, Any]) -> Event | None:
        """Per-source row parser — a module-level function (assigned via
        staticmethod) so parser tests call it directly with fixture dicts."""

    def fetch(self) -> Iterable[Event]:
        """Paginate the Tribe REST API, yielding kid-relevant Events."""
        now = datetime.now(UTC)
        start_date = now.strftime("%Y-%m-%d %H:%M:%S")
        end_date = (now + timedelta(days=self.window_days)).strftime("%Y-%m-%d %H:%M:%S")

        total = 0
        page = 1
        while page <= self._max_pages:
            rows, next_url = self._get_page(page, start_date, end_date)
            if rows is None:
                # Hard error on this page — log already emitted in _get_page.
                break
            if not rows:
                break

            for row in rows:
                try:
                    ev = self._parse_row(row)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "%s: failed to parse event id=%r",
                        self.name,
                        row.get("id"),
                        exc_info=True,
                    )
                    continue
                if ev is not None:
                    total += 1
                    yield ev

            if not next_url:
                break

            page += 1
            time.sleep(self._delay)

        logger.info("%s: yielded %d events", self.name, total)

    def _get_page(
        self,
        page: int,
        start_date: str,
        end_date: str,
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Fetch one page of events.

        Returns (rows, next_rest_url) on success, or (None, None) on HTTP error.
        """
        params = {
            "per_page": self._per_page,
            "page": page,
            "start_date": start_date,
            "end_date": end_date,
            "status": "publish",
        }
        try:
            resp = cffi_requests.get(
                self._events_url,
                params=params,
                headers={"User-Agent": USER_AGENT},
                impersonate="chrome",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = list(data.get("events") or [])
            next_url = data.get("next_rest_url") or None
            return rows, next_url
        except Exception:  # noqa: BLE001
            logger.warning("%s: failed to fetch page %d", self.name, page, exc_info=True)
            return None, None
