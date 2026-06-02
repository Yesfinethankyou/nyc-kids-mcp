"""Seed hardcoded fake events for Checkpoint A.

Lets us prove the HTTP + auth + Claude-connector path before NYC Parks ingest
exists. Run with: `python -m nyc_events.seed_fake`. Delete after Checkpoint B.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .models import Borough, Event, Price, compute_id

NYC_TZ = ZoneInfo("America/New_York")
UTC = UTC

_SAMPLES = [
    dict(
        ext="brooklyn-bridge-story-hour",
        title="Story hour under the Brooklyn Bridge",
        description="Outdoor story time with songs for toddlers and preschoolers.",
        url="https://example.com/story-hour",
        start_offset_days=1, duration_hours=1,
        venue="Brooklyn Bridge Park", borough=Borough.BROOKLYN,
        neighborhood="DUMBO", age_min=2, age_max=6, price=Price.FREE,
        tags=["story time", "family", "outdoor"],
    ),
    dict(
        ext="prospect-park-nature-hike",
        title="Family nature hike at Prospect Park",
        description="Easy 1-mile guided hike. Bring water and a curious kid.",
        url="https://example.com/prospect-hike",
        start_offset_days=2, duration_hours=2,
        venue="Prospect Park Audubon Center", borough=Borough.BROOKLYN,
        neighborhood="Prospect Park", age_min=4, age_max=10, price=Price.FREE,
        tags=["nature", "family", "outdoor"],
    ),
    dict(
        ext="moma-kids-workshop",
        title="MoMA kids art workshop",
        description="Hands-on collage workshop for ages 4-7.",
        url="https://example.com/moma-kids",
        start_offset_days=3, duration_hours=2,
        venue="MoMA", borough=Borough.MANHATTAN,
        neighborhood="Midtown", age_min=4, age_max=7, price=Price.PAID,
        tags=["arts & crafts", "family"],
    ),
    dict(
        ext="queens-museum-music",
        title="Toddler music morning at Queens Museum",
        description="Sing-along with instruments. Kid-friendly seating.",
        url="https://example.com/queens-music",
        start_offset_days=5, duration_hours=1,
        venue="Queens Museum", borough=Borough.QUEENS,
        neighborhood="Flushing Meadows", age_min=1, age_max=4, price=Price.FREE,
        tags=["music", "family"],
    ),
    dict(
        ext="bronx-zoo-explorer",
        title="Bronx Zoo Explorer Day",
        description="Special docent-led tour for families with young kids.",
        url="https://example.com/zoo-explorer",
        start_offset_days=6, duration_hours=3,
        venue="Bronx Zoo", borough=Borough.BRONX,
        neighborhood="Bronx Park", age_min=3, age_max=8, price=Price.PAID,
        tags=["nature", "family"],
    ),
    dict(
        ext="si-childrens-museum",
        title="Staten Island Children's Museum open play",
        description="Drop-in open play day. Best for ages 2-6.",
        url="https://example.com/si-museum",
        start_offset_days=4, duration_hours=4,
        venue="Staten Island Children's Museum", borough=Borough.STATEN_ISLAND,
        neighborhood="Snug Harbor", age_min=2, age_max=6, price=Price.PAID,
        tags=["best for kids", "family"],
    ),
]

SOURCE = "fake"


def fake_events(now: datetime | None = None) -> list[Event]:
    base = (now or datetime.now(NYC_TZ)).replace(hour=10, minute=0, second=0, microsecond=0)
    out: list[Event] = []
    for s in _SAMPLES:
        start = (base + timedelta(days=s["start_offset_days"])).astimezone(UTC)
        end = start + timedelta(hours=s["duration_hours"])
        out.append(
            Event(
                id=compute_id(SOURCE, s["ext"]),
                source=SOURCE,
                external_id=s["ext"],
                title=s["title"],
                description=s["description"],
                url=s["url"],
                start_dt=start,
                end_dt=end,
                venue_name=s["venue"],
                borough=s["borough"],
                neighborhood=s["neighborhood"],
                age_min=s["age_min"],
                age_max=s["age_max"],
                price=s["price"],
                tags=s["tags"],
            )
        )
    return out


def main() -> None:
    path = os.environ.get("DB_PATH", "data/events.db")
    events = fake_events()
    with db.connect_events(path) as conn:
        ins, upd = db.upsert_events(conn, events)
    print(f"Seeded {len(events)} fake events to {path}: {ins} inserted, {upd} updated")


if __name__ == "__main__":
    main()
