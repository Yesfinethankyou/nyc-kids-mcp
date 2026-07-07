"""Brooklyn Public Library parser tests.

Exercises the pure helpers and ``_parse_row`` directly against a REAL captured
slice of BPL's discover search index (tests/fixtures/bpl_sample.json, page 1
of ?event=true&view=grid). The parser takes a Solr doc dict, not an httpx
response, so there is no HTTP layer to mock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from nyc_events.models import Borough, Price
from nyc_events.sources.bpl import (
    _age_band,
    _extract_price,
    _is_kid_relevant,
    _normalize_tags,
    _parse_dt,
    _parse_row,
    _strip_html,
)

FIXTURES = Path(__file__).parent / "fixtures"
DOCS = json.loads((FIXTURES / "bpl_sample.json").read_text())
BY_ID = {d["item_id"]: d for d in DOCS}

# Known item_ids from the captured page (stable Drupal nids).
STORYTIME = "820758"  # ss_event_age "Birth to Five Years"
HOMEWORK = "820897"  # ss_event_age "Kids"
TEEN = "816727"  # ss_event_age "Teens & Young Adults"
ADULT_ESOL = "820487"  # ss_event_age "Adults" — must be filtered out
ADULT_BIRDING = "824253"  # ss_event_age "Adults" — must be filtered out


# --- Audience filter ---------------------------------------------------------


class TestKidRelevant:
    def test_birth_to_five_kids_teens_relevant(self):
        assert _is_kid_relevant({"ss_event_age": "Birth to Five Years"}) is True
        assert _is_kid_relevant({"ss_event_age": "Kids"}) is True
        assert _is_kid_relevant({"ss_event_age": "Teens & Young Adults"}) is True

    def test_adult_bands_not_relevant(self):
        assert _is_kid_relevant({"ss_event_age": "Adults"}) is False
        assert _is_kid_relevant({"ss_event_age": "Older Adults"}) is False

    def test_unknown_band_falls_back_to_keywords(self):
        assert _is_kid_relevant({"ss_event_age": "Family Program"}) is True
        assert _is_kid_relevant(
            {"ss_event_age": "", "ts_title": "Toddler Storytime"}
        ) is True
        assert _is_kid_relevant(
            {"ss_event_age": "", "sm_event_tags": ["preschool"], "ts_title": "X"}
        ) is True

    def test_no_signal_not_relevant(self):
        assert _is_kid_relevant({"ss_event_age": "", "ts_title": "Quiet Study Room"}) is False
        assert _is_kid_relevant({}) is False

    def test_midword_substring_does_not_admit_adult_event(self):
        # "kid" must not match "kidney" in the title fallback (issue #40/#62).
        assert _is_kid_relevant(
            {"ss_event_age": "", "ts_title": "Kidney Health Screening"}
        ) is False
        # Real whole words still admit the event.
        assert _is_kid_relevant(
            {"ss_event_age": "", "ts_title": "Kids Craft Hour"}
        ) is True


# --- Age band mapping --------------------------------------------------------


class TestAgeBand:
    def test_known_bands(self):
        assert _age_band("Birth to Five Years") == (0, 5)
        assert _age_band("Kids") == (5, 12)
        assert _age_band("Teens & Young Adults") == (13, 18)

    def test_case_insensitive(self):
        assert _age_band("kids") == (5, 12)

    def test_adult_and_unknown_have_no_band(self):
        assert _age_band("Adults") == (None, None)
        assert _age_band(None) == (None, None)


# --- HTML / date / price helpers ---------------------------------------------


class TestStripHtml:
    def test_strips_tags_entities_and_whitespace(self):
        out = _strip_html("<div><p>Hello&nbsp;there &amp; welcome</p>\n\n</div>")
        assert out == "Hello there & welcome"

    def test_empty(self):
        assert _strip_html(None) == ""
        assert _strip_html("") == ""


class TestParseDt:
    def test_z_suffix(self):
        assert _parse_dt("2026-06-05T14:30:00Z") == datetime(2026, 6, 5, 14, 30, tzinfo=UTC)

    def test_offset_and_garbage(self):
        assert _parse_dt("2026-06-05T14:30:00+00:00") == datetime(2026, 6, 5, 14, 30, tzinfo=UTC)
        assert _parse_dt(None) is None
        assert _parse_dt("nope") is None


class TestPrice:
    def test_free_by_default(self):
        assert _extract_price("A free library program") == Price.FREE
        assert _extract_price("") == Price.FREE

    def test_dollar_is_paid(self):
        assert _extract_price("Materials fee $5 per child") == Price.PAID


class TestNormalizeTags:
    def test_lowercases_bpl_tags_and_adds_inferred(self):
        doc = {"sm_event_tags": ["Storytime", "Early Literacy"]}
        tags = _normalize_tags(doc, "Toddler Storytime", "songs and crafts")
        assert "storytime" in tags
        assert "early literacy" in tags
        # inferred from keywords, not duplicated
        assert "story time" in tags
        assert tags.count("storytime") == 1


# --- Row parsing (end-to-end against the real fixture) -----------------------


class TestParseRow:
    def test_happy_path_storytime(self):
        ev = _parse_row(BY_ID[STORYTIME])
        assert ev is not None
        assert ev.source == "bpl"
        assert ev.title == "Storytime"
        assert ev.external_id == STORYTIME
        assert ev.url == "https://www.bklynlibrary.org/node/820758"
        assert ev.start_dt == datetime(2026, 6, 5, 14, 30, tzinfo=UTC)
        assert ev.venue_name == "Cypress Hills Library"
        assert ev.borough == Borough.BROOKLYN
        assert ev.price == Price.FREE
        assert (ev.age_min, ev.age_max) == (0, 5)
        assert ev.description and "<" not in ev.description  # HTML stripped
        assert len(ev.tags) > 0
        assert ev.raw_payload is not None

    def test_age_bands_from_fixture(self):
        assert _parse_row(BY_ID[HOMEWORK]).age_max == 12  # Kids
        assert _parse_row(BY_ID[TEEN]).age_min == 13  # Teens

    def test_adult_rows_dropped(self):
        assert _parse_row(BY_ID[ADULT_ESOL]) is None
        assert _parse_row(BY_ID[ADULT_BIRDING]) is None

    def test_canceled_dropped(self):
        row = dict(BY_ID[STORYTIME])
        row["is_event_canceled"] = 1
        assert _parse_row(row) is None

    def test_missing_title_dropped(self):
        row = dict(BY_ID[STORYTIME])
        row["ts_title"] = ""
        assert _parse_row(row) is None

    def test_start_date_falls_back_to_epoch(self):
        row = dict(BY_ID[STORYTIME])
        row.pop("ds_event_start_date", None)
        ev = _parse_row(row)
        assert ev is not None
        assert ev.start_dt == datetime.fromtimestamp(row["is_event_start_date"], tz=UTC)

    def test_stable_id_deterministic_16_hex(self):
        a = _parse_row(BY_ID[STORYTIME])
        b = _parse_row(BY_ID[STORYTIME])
        assert a.id == b.id and len(a.id) == 16

    def test_external_id_is_item_id(self):
        ev = _parse_row(BY_ID[HOMEWORK])
        assert ev.external_id == HOMEWORK

    def test_raw_payload_round_trips(self):
        ev = _parse_row(BY_ID[STORYTIME])
        assert json.loads(ev.raw_payload)["item_id"] == STORYTIME

    def test_fixture_keeps_only_kid_family_rows(self):
        kept = [ev for d in DOCS if (ev := _parse_row(d)) is not None]
        # 20 captured docs; the 10 adult-band rows are filtered out.
        assert len(kept) == 10
        assert all(ev.borough == Borough.BROOKLYN for ev in kept)
        assert all(ev.source == "bpl" for ev in kept)
