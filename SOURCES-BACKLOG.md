# Future source backlog (candidates — verify before building)

Research notes for NYC venues proposed for Phase 2 integration. Probe a
CANDIDATE, confirm its format + endpoint, then run the `source-adder` recipe.
Entries are grouped by status: **Ready to build**, **Low confidence**,
**Built** (as-built notes), and **Rejected**.

## Status legend

- `CANDIDATE` — plausible source found, format guessed or partially confirmed.
- `CONFIRMED` — probed; format + endpoint verified.
- `BUILT` — shipped as an enabled source; entry kept for as-built history.
- `REJECTED` — probed, but no usable feed OR the content isn't kid-relevant.

## Cross-cutting notes

**Anti-bot 403s.** Consumer-facing sites (Industry City, Domino, Green-Wood,
Governors Island, Coney Island, Brooklyn Army Terminal) return 403 to plain
fetchers — expect to need `curl_cffi` (`impersonate="chrome"`) for all of
them. The MLB Stats API is the sole exception (it's a JSON API, not a page).

**Sandbox egress varies — try first, don't assume.** Earlier guidance here
said cloud/web sessions can't reach these domains. That's been wrong in
practice: green-wood.com, prospectpark.org, nytransitmuseum.org, and
coneyisland.com were all probed and fixture-captured directly from a web
session. Try the probe from the sandbox first; only fall back to capturing
on your laptop/NAS if a specific domain is actually blocked.

**⚠️ `curl_cffi` impersonation is BROKEN in the Claude-Code-on-web sandbox
(2026-07-13).** This environment routes outbound HTTPS through a
TLS-re-terminating MITM proxy, and `curl_cffi`'s browser-TLS impersonation
(`impersonate="chrome"`) connection-resets through it — verified failing even
against `example.com`, while plain `httpx` and non-impersonated `curl_cffi`
work fine. **Consequence:** any anti-bot candidate that needs the impersonated
fingerprint to get past a WAF/Cloudflare/Incapsula wall **cannot be probed or
fixture-captured from a web session** — the source recipe (real captured
fixture + parser test) can't be completed here. Those must be built from a
non-proxied session (laptop/NAS). Confirmed blocked here on 2026-07-13:
- **Queens Public Library** (`queenslibrary.org/calendar`) — F5/BIG-IP "Request
  Rejected" WAF wall to plain httpx.
- **NYPL** (`nypl.org/events/calendar`) — Imperva **Incapsula** JS-challenge.
- **AMNH** / **The Met** / **Intrepid** — 403 / 429 (Cloudflare-class).
- **City Parks Foundation** — 403 Cloudflare.
These are still the highest-value unbuilt candidates (the two library systems
have neighborhood coding already done); they're just not buildable *in a web
session* until `curl_cffi`-through-proxy is fixed or the build is done
elsewhere.

**Off-proxy re-probe (2026-07-13, laptop, `curl_cffi impersonate="chrome"`
verified working): every wall above fell.** Per-entry findings below, summary:
**QPL** and **NYPL** are CONFIRMED (both server-render their listings once
impersonated); **City Parks Foundation** is a standard WordPress/Tribe site
with the REST API open (CONFIRMED — cheapest build of the batch);
**Intrepid**'s calendar is a Drupal view whose `/views/ajax` endpoint answers
directly; the **Macaroni Kid Yodel widget** server-renders a JSON-LD
`ItemList` of events; **AMNH** and **the Met** are reachable but their full
calendars are JS/RSC-driven — still no cheap parse, deprioritized. Fixture
capture for all of these must happen from a non-proxied session (laptop/NAS);
the nightly ingest already runs off-proxy on the NAS, so runtime fetches are
fine.

**Re-probe results (2026-07-13, plain httpx, this session):**
- **Snug Harbor** → ✅ BUILT (see its entry below — clean WP REST + JSON-LD).
- **Brooklyn Bridge Parents** → **weak, deprioritized.** WP Event Manager
  (`/wp-json/wp/v2/event_listing`) works, but only **9 events**, and the
  sampled rows are **re-posts of our existing `brooklyn_army_terminal`
  source** ("Summer at the Terminal: …") — it's an aggregator with heavy
  dedup risk against sources we already run, not net-new coverage.
- **Puppetworks** → **rejected here (JS-rendered).** Runs on the `edit.site`
  website builder (`/bundle/publish/…/bundle.js`, `fonts-cdn.edit.site`); the
  schedule is client-rendered, so plain httpx gets only CSS/font boilerplate.
  Headless-browser tier — out of scope.
- **NYSCI** → no `/events` or Tribe route; homepage embeds Eventbrite. Would
  need an Eventbrite-organizer probe (unattempted). Deprioritized.
- **BAM** → homepage `#Calendar` is a JS SPA anchor; no wp-json / JSON-LD
  `Event` on a plain fetch. Likely headless-tier — deprioritized.

## ✅ Major reassessment: nycgovparks.org/events is alive and far richer than tvpp-9vvx — BUILT

- **Status:** ✅ **BUILT 2026-07-06** — shipped as source `nycgovparks_events`
  (`src/nyc_events/sources/nycgovparks_events.py`); as-built notes at the end
  of this section. Previously: 🟢 CONFIRMED + VERIFIED 2026-07-06
  (source-verifier pass same
  day — all four open questions answered below, fixtures captured). This is a
  significant finding, flagged prominently rather than buried as one more
  CANDIDATE: the live NYC Parks events **website** looks substantially better
  than the permit registry (`tvpp-9vvx`) currently powering the Phase 1
  source, and was never actually probed — only its Open Data export was.
- **Why we're on `tvpp-9vvx` today (the actual history, from `nyc_permitted_events.py`
  and README):** the original Phase 1 spec named the NYC Parks Events Listing
  **Open Data dataset** `fudw-fgrp`. That SODA dataset is genuinely frozen
  (last row 2019-12). The prior session concluded from that fact alone that
  "NYC Parks events" as a *data source* was dead, and pivoted to `tvpp-9vvx`
  (the citywide permit registry — broader, noisier, no descriptions, no
  categories, no cost, no lat/lng) as the "live successor." **That
  investigation stayed entirely inside the Open Data catalog and never
  fetched `nycgovparks.org/events` directly** — the live website is run by
  NYC Parks' own web team and is a separate system from whatever Socrata
  mirror they used to publish (and stopped publishing in 2019).
- **What the live re-probe found:** `https://www.nycgovparks.org/events` is
  very much alive — **10,964 events** listed out to March 2029 at probe time.
  It uses real **schema.org `Event` microdata** embedded in server-rendered
  HTML (`itemscope itemtype="http://schema.org/Event"`), which `tvpp-9vvx`
  has none of:
  - `itemprop="name"` (title), `itemprop="description"` (real free text —
    100% of a 50-row sample had one; `tvpp-9vvx` has **zero** descriptions),
  - `meta itemprop="startDate"`/`endDate"` — full ISO-8601 **with UTC
    offset** (`2026-07-06T07:00:00-04:00`) — no ambiguous-timezone parsing
    needed, unlike several existing sources,
  - `itemprop="location"` → nested `Place`/`PostalAddress` with
    `streetAddress` **and** `addressLocality` (the borough name, e.g.
    "Staten Island") directly on the list page,
  - **detail pages additionally carry `itemprop="latitude"`/`"longitude"`
    geo coordinates directly** (verified on a sample event) — this source
    would need **zero enrich-pass geocoding** for these rows, unlike every
    other venue source in the catalog,
  - a real **`Category:` taxonomy** curated by NYC Parks staff — dozens of
    categories including `arts-and-crafts`, `nature`, `birding`, `STEM`,
    `gardening`, `urbanparkrangers`, `festivals`, `waterfront`, `theater`,
    and critically **`kids`** (rendered as "Best for Kids" with its own
    highlighted `pearls-pick-box` callout on list rows) — a genuine editorial
    kid-relevance judgment from NYC Parks itself, not our own keyword
    inference,
  - cost info ("Free!" seen on every sampled kids row; presumably populated
    for paid programs too — not yet sampled),
  - registration status (e.g. "Registration is closed") and instructor name
    on detail pages.
- **The kids category is directly URL-addressable and already
  date-windowed:** `https://www.nycgovparks.org/events/kids` returned
  **2,427 events** covering "July 6, 2026 to August 31, 2026" — a ~56-day
  rolling window server-side, close to the existing `days_ahead=60`
  convention — with **zero client-side filtering needed** to get to
  kid-relevant rows. All 4 sampled boroughs (Queens, Manhattan, Bronx,
  Staten Island) appeared in a single 50-row page; Brooklyn simply didn't
  land in that particular page, not evidence of a gap.
- **Pagination confirmed:** path-based, `/events/kids/p2`, `/events/kids/p3`,
  etc. (verified `p2` returns 200) — not a query param, easy to miss if you
  only grep for `page=`.
- **No standalone JSON/RSS/iCal endpoint found** (`/events.rss`, `/events/rss`,
  `/events.xml`, `/events.json`, `/events.ics`, `/sitemap.xml` all 404) —
  this is an HTML-microdata scrape (selectolax against `itemprop`
  attributes), not a REST API. That's a very tractable scrape though —
  arguably easier than most of our Tribe parsing since the fields are
  individually tagged by `itemprop`, not positionally inferred from prose.
  **BUT the machine-readable surface exists in-page** — see the
  `eventsByLocationJSON` finding below.
- **This is not just "one more candidate"** — if built, it would plausibly
  **replace or sit alongside `tvpp-9vvx` as the Parks-Department event
  source**, with: real descriptions (permit source has none), a real
  category taxonomy including an NYC-Parks-curated "kids" tag (permit source
  relies on brittle keyword-matching a noisy permit title), free lat/lng on
  detail pages (permit source needs the full enrich geocoding pipeline), and
  precise borough/address (permit source's `event_location` needs regex
  cleanup — see `_clean_venue`). The tradeoff: it's an HTML scrape of a
  government site (could break on a redesign) rather than a versioned Open
  Data API. ~~Per-event detail-page fetches would be needed~~ — **wrong,
  see verification finding 3 below: the list page alone carries a complete
  Event row, INCLUDING lat/lng via the embedded `eventsByLocationJSON`
  blob** (only the untruncated description lives exclusively on detail
  pages, and it's optional — list snippets are ~185 chars, and listing-tool
  summaries truncate at 200 anyway).
- **Verification pass (2026-07-06) — the four open questions, answered:**
  1. **Overlap with `tvpp-9vvx`: effectively ZERO — complementary, not
     duplicative.** Same-day comparison (2026-07-07): the permit registry had
     1,025 Parks-Department rows, almost all third-party field reservations
     ("Baseball - 12 and Under (Little League)", bootcamps, maintenance
     closures, protests); `/events/kids` that day was NYC Parks' own
     programming (Kids in Motion at ~40 playgrounds, rec-center summer camps,
     Summer Sports Experience, ranger events). Exact- and fuzzy-name
     intersection of the two samples: **empty**. Build it *alongside*
     `tvpp-9vvx`, no dedup needed. *(Epilogue 2026-07-12: `tvpp-9vvx` was
     disabled anyway — maintainer found the permit rows unused in practice
     once this source shipped. Zero-overlap means dropping it loses the
     field-reservation-style permits entirely, which is exactly what was
     wanted. Module kept; see `nyc_permitted_events.py` docstring.)*
  2. **Category vocabulary: ~50 slug categories** (`/events/<slug>` — `nature`,
     `urbanparkrangers`, `arts-and-crafts`, `education`, `wildlife`, `games`,
     `festivals`, `astronomy`, `fishing`, …; filter form posts `cat_id[]`,
     kids = **18**). Categories are **multi-tag** and the `kids` tag is
     well-applied: kid-targeted events found via *other* categories ("Nature
     Story Time" via nature/rangers, "Foragers in the Foodway" via education)
     also carried `kids`. One borderline miss observed: "Basic Canoeing and
     Nature Exploration" (description says "Ages 8 and up") is
     nature/rangers-only. **`/events/kids` alone is the right v1 fetch**;
     a supplemental `urbanparkrangers`/`nature` pass with our keyword filter
     is a possible later enhancement, not a launch requirement. Bonus: each
     list card's *class list* carries its category ids (`class="row event
     cat18 cat205 cat211"`), so tags are extractable from the list page.
  3. **Detail-page fetches: NOT needed.** A list card alone carries:
     **numeric event id** (`<h3 id="event_title__2205424">`), title,
     per-occurrence URL, `meta` startDate/endDate (full ISO + offset), venue
     (`Place > itemprop="name"`, e.g. "Multi-Use Room (in Alfred E. Smith
     Recreation Center)"), `streetAddress` (sometimes empty),
     `addressLocality` = borough, a **~185-char truncated** description,
     cost line ("Free!"), category ids, accessibility icon, and the
     `pearls-pick` flag. **And lat/lng is on the list page too**, via the
     embedded map-widget blob (next bullet) — only the **full untruncated
     description** requires a detail-page fetch; skip it for v1.
     `/events/kids` = **49 pages ≈ 2,430 events** (last page p49 had 30
     rows; **p50 returns HTTP 200 with 0 event blocks** — terminate on an
     empty page, not a 404; window 2026-07-06 → 2026-08-31, ~56 days) —
     49 list requests/night, no per-event fetches.
  4. **IDs / recurrence: per-occurrence numeric ids — no `compute_id`
     override needed.** Recurring programs get a distinct id AND a distinct
     dated URL per occurrence (Kids in Motion @ Anne Loftus Playground:
     2026-07-07 = id 2192210 at `/events/2026/07/07/…`, 2026-07-09 =
     id 2192170 at `/events/2026/07/09/…`; same-day repeats get slug
     suffixes like `…-pickleball1`). Use the numeric id as `external_id`
     as-is. Stability: no anti-bot (plain `httpx` + browser UA; robots.txt
     does not disallow `/events` for generic agents), 49 sequential fetches
     drew no throttling during verification.
- **Machine-readable alternative: YES, embedded in-page (missed on the
  first sweep).** No standalone RSS/iCal/JSON endpoints, but **every
  `/events/...` list page embeds `var eventsByLocationJSON = [...]`** in a
  `<script>` block (~518 KB on `/events/kids`) — a map-widget JSON payload
  containing the **entire current window, not just that page's 50 rows**:
  119 venues × 2,430 events at probe time. Per venue: `name`, `link`
  (facility page), `address`, `borough`, `accessible`, **`lat`/`lng`**
  (present on all 119 venues). Per event: `title`, `startDate`/`endDate`
  (**epoch milliseconds**), `repetitionString` (null across the whole
  sample), and `link` — the per-occurrence detail path, i.e. a perfect join
  key against the microdata cards' anchor hrefs. The blob lacks description
  and cost (those are microdata-only), so the build is: paginate the
  microdata cards, join the page-1 blob by `link` for lat/lng +
  parent-facility venue name + accessible flag. **Coordinates therefore
  come free from the list fetch — zero geocoding needed for this source.**
  Raw blob uses PHP-style `\/` escaping (transparent to `json.loads`).
- **Fixtures captured:** `tests/fixtures/nycgovparks_events_kids_page.html`
  (real `/events/kids` p1, trimmed to: the `eventsByLocationJSON` script
  reduced to its first 6 venues, the `#catpage_events_list` container +
  first 10 cards, and the `parks_pages` pagination markup — full page is
  ~630 KB) and `tests/fixtures/nycgovparks_event_detail.html` (full detail
  page, incl. `itemprop="latitude"/"longitude"` and the category link list).
- **Build parameters for `source-adder`:**
  - Fetch: `GET https://www.nycgovparks.org/events/kids` then `/events/kids/p2`…
    until a page yields 0 cards (~49 pages; p50 is HTTP 200 with 0 cards,
    not a 404); plain `httpx` + browser UA (note: `curl_cffi` sometimes gets
    connection resets in the sandbox where httpx succeeds — prefer httpx);
    1s polite delay between pages.
  - Parse: split on `itemscope itemtype="http://schema.org/Event"` cards;
    fields per finding 3 above. Date headers (`<h2 id="YYYY-MM-DD">`) are
    redundant with the per-card `meta startDate` — ignore them. Dates are
    ISO-8601 with offset — `datetime.fromisoformat` directly.
  - **Blob join for lat/lng + venue:** regex
    `var eventsByLocationJSON = (\[.*?\]);` on page 1 → `json.loads` →
    build `link → (lat, lng, parent-venue name, borough, accessible)`
    (blob covers the whole window, so page 1 alone suffices). Prefer the
    blob's top-level venue name (`Greenbelt Recreation Center`) over the
    microdata Place name, which is sometimes a sub-room (`Multi-Use Room`);
    the parent name also lines up with the `park_neighborhoods.json` tier.
  - `external_id` = the numeric id from `event_title__<id>`.
  - Price: cost-line text `Free!` → `Price.FREE`; else `Price.UNKNOWN`
    (paid formatting never observed in the 50-row sample).
  - Kid-filter: **none** (Parks-curated category, like `mommy_poppins`) —
    but keep the shared `ADULT_BLOCKLIST` import as a cheap safety net.
    One edge case: titles prefixed **`CANCELLED:`** appear in the feed
    (observed live) — skip those rows at parse time (explicit upstream
    cancellation beats our `possibly_cancelled` heuristic).
  - `window_days = 55` (server window is "today → end of next month",
    56 days at probe, varies ~55–61 by calendar — use the conservative
    lower bound) and **opt IN to missing-detection** — the feed re-lists
    its entire window every fetch (full-window source, unlike
    `mommy_poppins`' incremental sitemap).
  - `neighborhood=None` from the source (enrich pass codes it; rows arrive
    with lat/lng so tier 5 reverse-geocode covers anything the park table
    misses — no forward geocoding). Borough from `addressLocality`.
  - Rows will be `low_confidence: false` (real description + URL) — this
    single source roughly doubles the catalog's curated-event count;
    sanity-check ingest totals and search behavior after the first run.
- **As-built notes (build 2026-07-06; spec above followed as written, plus):**
  - **Category-id → tag table resolved live** (the one thing the spec left
    open): card class lists carry `catNN` ids but the kids-page cards have no
    "Category:" text line (only `/events` all-page cards do). The full id→slug
    mapping was solved by intersecting class-id sets across `/events` p1–p8
    (400 cards, using each card's Category link line as constraints) plus 10
    per-category page probes (`/events/<slug>` — every card there carries that
    category's id). 33 ids mapped in `_CATEGORY_TAGS` (2 arts-and-crafts,
    4 birding, 5 education, 7 concerts, 9 dance, 10 nature, 11 exhibits/Art,
    12 festivals, 13 film, 14 fitness, 15 games, 17 history, **18 kids**,
    20 markets, 23 pets, 25 sports, 27 theater, 28 tours, 29 volunteer,
    47 urbanparkrangers, 100 food, 102 kayaking, 105 shape-up-nyc, 106 talks,
    109 waterfront, 121 outdoor-fitness, 125 astronomy, 128 fishing,
    137 summer-sports-experience, 147 hiking, 167 wildlife, 303 gardening);
    audience/venue-type ids (122 seniors, 205 recreation-centers, 206/211/291
    internal markers) deliberately unmapped. Unknown ids are skipped silently.
  - **Blob venue name is the park PROPERTY**, one level above even the
    "(in …)" parent shown in microdata for playgrounds: "Kids In Motion:
    Addabbo Playground" has Place "Addabbo Playground (in Tudor Park)" and
    blob venue **"Tudor Park"** — exactly what `park_neighborhoods.json`
    keys on. Fallback order when a link isn't in the blob: "(in <parent>)"
    text, then Place name.
  - Smoke test (2 live pages, 2026-07-06): 100/100 cards parsed, 100/100
    joined blob lat/lng, all five boroughs present, all rows Free!.
    ~49 pages × 50 cards ≈ **2,430 events/run** expected.
  - **Known residue:** ~1% of rows (e.g. "Queens Recreation Summer Sports
    Experience" at a bare "Play Area") have no `addressLocality` AND a null
    blob borough → `borough=None`. They still get lat/lng from the blob, so
    the enrich tier-5 reverse geocode codes their neighborhood; not worth
    importing the coordinate bounding-box machinery for.
  - `test_missing_detection.py::test_full_window_sources_opt_in` extended:
    the opted-in census is now 10 sources and this one is the first whose
    window isn't 60 (55, mirroring the server's ~55–61-day rolling window).
  - No `age_min`/`age_max` (ages live in description prose only); price is
    FREE on the "Free!" cost line else UNKNOWN (paid formatting never
    observed).

## Tech debt / TODO

**Review filter lists for all sources — DONE (maintainer review, 2026-06).**
The `FILTER-REVIEW.md` worksheet that inventoried every filter was deleted
after the review (its outcome lives in `sources/_filters.py` and the source
modules); the review's decisions were
applied as a focused pass with fresh live fetches per touched source:
- Alcohol-tasting terms dropped from every blocklist (alcohol alone isn't an
  adult-only signal).
- Shared adult signals hoisted into `src/nyc_events/sources/_filters.py`
  (`ADULT_BLOCKLIST` / `MEMBERS_ONLY` + a `normalize()` that collapses
  hyphen/space variants), imported by the six editorial sources; per-source
  extras stay local.
- Green-Wood's dead soft-blocklist removed; its adult terms promoted to the
  hard-exclude.
- Tag inference word-boundary-matched across all keyword-tagging sources so
  short keywords stop matching mid-word.
- `drag show`/`drag brunch` moved to a title-only shared set
  (`ADULT_TITLE_BLOCKLIST`) so a family event whose body merely mentions an
  adjacent drag show isn't dropped; the core adult terms still match title+body.

## How to verify

```python
# pip install curl_cffi
from curl_cffi import requests

def probe(url):
    r = requests.get(url, impersonate="chrome", timeout=30)
    h = r.text.lower()
    print(f"\n{url}\n  HTTP {r.status_code}  len={len(r.text)}")
    for tell in ["application/ld+json", "tribe-events", "/wp-json",
                 "wp-content", "squarespace", "static1.squarespace",
                 "drupal", "eventbrite", "dice.fm", "tessitura",
                 'rel="alternate" type="application/rss"', ".ics", "ical"]:
        n = h.count(tell)
        if n:
            print(f"    {tell!r}: {n}")

for u in [
    "https://industrycity.com/events/",
    "https://www.dominopark.com/events",
    "https://govisland.com/calendar",
    "https://www.brooklynarmyterminal.com/events",
]:
    probe(u)
```

---

## Candidates — to probe (Phase 3 venue expansion)

Fresh leads, not yet probed. Run `source-verifier` (or the probe snippet above)
to classify the platform and capture a fixture, then flip to CONFIRMED/REJECTED.

**Note:** Brooklyn Children's Museum is already **BUILT** (source
`bk_childrens_museum`, live in `ENABLED_SOURCES`) — not re-added here.

### Staten Island Children's Museum — ✅ BUILT 2026-07-13

- **Status:** ✅ **BUILT 2026-07-13** — shipped as source `si_childrens_museum`
  (fifth `TribeEventsSource` subclass). As-built notes:
  - Live re-verification matched the 7-06 research exactly: standard Tribe
    shape, 64 events / 2 pages, single venue. **Per-occurrence ids verified
    live** (recurring "Walk-In! Workshop" rows carry distinct ids 9400/9413/…
    per date) → `external_id = str(id)`, no suffix.
  - **Price quirk found during build:** `cost` is empty on every row; the
    venue's "Free" *category* is the real free-admission signal →
    `_resolve_price` maps category Free → Price.FREE, else UNKNOWN.
  - Curated-kids posture (all 64 live titles spot-checked kid programming);
    only the shared adult/members-only title net kept as a defensive guard
    (children's museums do run occasional 21+ fundraiser nights).
  - Tags are category-driven (the Tribe taxonomy is venue-curated: "Event
    for Kids", "STEM", "Art-Making", …). `SOURCE_NEIGHBORHOOD` = "Snug
    Harbor". Opted into missing-detection (full-window Tribe re-fetch).
- **Original research (2026-07-06):** CONFIRMED (live probe, plain `httpx`, no anti-bot).
  **Highest-value find of this batch** — Staten Island currently has close to
  zero coverage in the catalog.
- **Source:** WordPress + The Events Calendar (Tribe) REST API — the same
  plugin as Green-Wood/Prospect Park/NY Transit/Industry City. Copy-adapt
  `_tribe.py`'s `TribeEventsSource`, don't write a new fetch loop.
- **Endpoint:** `https://sichildrensmuseum.org/wp-json/tribe/events/v1/events`
  — confirmed live, `total: 51`, `total_pages: 2` at `per_page=50` (near-term
  window; category counts on individual terms run into the hundreds, so the
  full historical catalog is much larger — expect a healthy 60-day window).
- **Venue confirmed single-site:** every sampled event's `venue.venue` reads
  "Staten Island Children's Museum" → a `SOURCE_NEIGHBORHOOD` constant is
  sufficient (Snug Harbor Cultural Center campus, Livingston).
- **Filtering plan:** likely little/no filter needed — a children's museum's
  own event calendar is kid-relevant by construction (same posture as
  `bk_childrens_museum`/`mommy_poppins`). Spot-check for members-only/rental
  events before shipping with zero filter, same caution applied to the other
  curated feeds.
- **Sample event confirms real fields:** title, `start_date`, `cost` (present
  but often empty), `categories` (Tribe taxonomy — "art", "Art-Making",
  "crafts" seen live), venue object. Standard Tribe shape — no surprises
  expected relative to the four sources already built on `_tribe.py`.
- **Next step:** straight to `source-adder` — this is a same-day build, no
  further verification needed.

### New York Family — events.newyorkfamily.com — ✅ BUILT 2026-07-12 (day-walk crawler over a deliberately hobbled API)

- **Status:** ✅ **BUILT 2026-07-12** — shipped as source `new_york_family`
  (`src/nyc_events/sources/new_york_family.py`); as-built notes at the end of
  this section. Same-day sequence: re-verified (findings below), maintainer
  chose the day-walk-crawler build over the lossy 16/day version or parking
  it. The verification record is kept verbatim because it documents the API
  quirks the build depends on.
- **Re-verification (2026-07-12, live, plain `httpx`, no anti-bot):** the
  7-06 "fifth Tribe copy-adapt + city allowlist" framing was **obsolete** —
  the network operator (Schneps Media) has crippled the Tribe REST API, and
  it changed *between the two probes* (7-06 saw a `total: 51` envelope;
  7-12 has no envelope at all), so it is under active modification. Both
  original open questions (geo filter, stubs) were solved — but a new,
  bigger problem replaced them.
- **What the API actually does now (all verified live 2026-07-12):**
  - Response envelope is `{"events": [...]}` only — no `total`/
    `total_pages`/`next_rest_url`. The `TribeEventsSource` pagination loop
    (keyed on `next_rest_url`) can never advance.
  - **`per_page` and `page` are ignored; every query returns at most 16
    rows** (the route's self-documented `per_page` default is the string
    `"16"`). Not a CDN cache artifact — cache-busters don't change it.
  - **`page>1` returns the SAME rows as page 1, serialized as empty husks**
    `{"start_date", "end_date"}` (verified: identical start-time multiset).
    This fully explains the 7-06 probe's "20% bare stubs" — they're not
    malformed recurrences, they're what any page>1 fetch gets. A day-walk
    fetch never requests page>1; keep a skip-if-no-`id`/`title` guard anyway.
  - **`start_date`/`end_date`/`categories` ARE honored** (smells like a
    REST-cache param allowlist). `ticketed` returns nothing useful.
  - `start_date` has **"ongoing at" semantics** — all-day and multi-day
    events return for every instant they span, and results are sorted by
    start ascending, so ongoing rows permanently occupy the head of the
    16-row window. A within-day time cursor therefore advances only slowly;
    a naive `start_date` cursor walk gets stuck entirely.
  - Rows have **no `utc_start_date`/`utc_end_date`** (both null) — only
    local `start_date` + a `timezone` field. `_tribe.parse_row` keys on
    `utc_start_date`, so it would drop every row. Only `strip_html`/
    `parse_cost` are reusable from `_tribe.py`; the fetch loop and row
    skeleton are not.
- **No clean side door (all checked 2026-07-12):** the event pool is shared
  across the Schneps network (row meta: `schneps_events_site_url =
  events.amny.com`); the hub API is hobbled identically (16-row cap, no
  envelope). `wp/v2/tribe_events` honors pagination but carries no
  occurrence dates (post objects only). The iCal export (`/events/?ical=1`)
  returns one stale 2025 event. Categories endpoint
  (`/tribe/events/v1/categories`) is fully functional — 58 slugs,
  network-wide counts.
- **True volume vs. what one query sees:** Saturday 2026-07-18, union across
  the base query + all 49 event-bearing category slices = **69 distinct
  (id, start) rows; the base query alone returns 16 (23%)**, systematically
  biased to all-day/morning events (first-16-by-start-time). Six categories
  hit the 16-cap themselves that day (`family-kids`, `free`, `kids`,
  `teens`, `tweens`, `attractions`), so even a full category union has
  residual silent truncation on busy days. Weekday volume not sampled but
  presumably lower (some days may fit in one query).
- **Geo problem: SOLVED — use coordinates, not city strings.**
  `venue.geo_lat`/`geo_lng` was present on **100% of 85 sampled rows**
  (the 7-06 "60% no city" figure came from counting page>1 husks). Classify
  five-borough membership by the `mommy_poppins.py` coordinate bounding
  boxes, with a city-string allowlist as fallback; drop non-NYC. ~72% of the
  7-18 union is five-borough; the rest is Long Island/East End (Huntington
  Station, Long Beach, Bridgehampton…). City strings alone are a trap:
  "New York City", "new york", "Manhatten" (sic), "Woodhaven",
  "Springfield Gardens" all appear.
- **Age bands: CONFIRMED, the unique payoff.** Category names carry
  structured bands — `Baby & Toddler (0–2)`, `Preschoolers (3–4)`,
  `Kids (5–8)`, `Tweens (9–12)`, `Teens (13–18)` — mappable to
  `age_min`/`age_max` (min-of-mins/max-of-maxes when several appear). No
  current source has structured ages. `Family` is on **100% of rows** (the
  site itself is the family filter — network events without a family tag
  don't syndicate here), so no kid-relevance filter is needed beyond the
  shared adult blocklists as a safety net (one `Nightlife`-tagged row seen).
- **Recurrence / external_id — differs from the four built Tribe sources:**
  the server expands recurring events per-occurrence *per queried day*, but
  occurrences share the parent's numeric `id` ("The Very Hungry Caterpillar
  Show" id 853667 appears at 11:30 AND 15:30 the same day; "Summer of
  Moomin" id 858274 appears daily through Sept). So
  `external_id = f"{id}:{start.isoformat()}"` (the permit-source pattern) is
  **mandatory**, not optional. A `recurrence` rules blob exists on rows but
  never needs client-side expansion — the day queries do it.
- **Build design, if built:** day-walk the window (one `start_date=<day>
  00:00:00&end_date=<day> 23:59:59` query per day), then, per day, either
  (a) adaptive time-slices (re-query with `start_date=<day> HH:00` while the
  previous slice returned 16 rows) or (b) a curated category fan-out —
  dedupe on `(id, start_date)` either way. Budget realistically
  **~200–600 requests/night** depending on design and window (vs ~49 for
  the next-heaviest source); a 28–35-day window instead of 60 halves it.
  Residual known loss: instants where >16 events are simultaneously ongoing.
  Full-window day-walk = opt IN to missing-detection if built.
- **Fragility warning:** the API shape changed in the six days between
  probes, in the direction of locking down. A source here rides on
  quirks (which params the cache honors) that Schneps can remove any week.
- **Verdict (as decided):** buildable without a headless browser, and the
  content is genuinely good (Manhattan coverage — the catalog's weakest
  borough — plus structured ages), but it is the heaviest, most fragile
  fetch in the codebase with documented incompleteness on peak days.
  Maintainer chose the full day-walk crawler (2026-07-12).
- **As-built notes (build 2026-07-12; design above followed, plus):**
  - **Fetch loop:** `NewYorkFamilySource` subclasses `Source` directly (NOT
    `TribeEventsSource` — see the module docstring for why nothing there
    fits). Day-walk of a **35-day window** (`window_days=35`, opted INTO
    missing-detection; the census test now expects 35 for this source), one
    base query per day + adaptive within-day slices: while a slice returns
    the 16-row cap, re-query with `start_date` advanced to the latest start
    seen (`_next_slice_start`), +2h when stuck (all visible rows ongoing),
    max 12 slices/day, dedupe on `(id, start_date)`. Plain `httpx` — no
    anti-bot on this API host.
  - **Smoke run (6 days, 2026-07-11→16):** 48 requests, 85 NYC events, zero
    duplicate ids, all five boroughs present, 100% rows with lat/lng, 26/85
    with age bands, free/paid ≈ 44/41. Every sampled July day hit the 16-cap
    several times (~8 slices/day) → expect **~280 requests and ~500 events
    per 35-day nightly run** (summer; winter likely fewer slices).
  - **Geo filter as built:** coordinate boxes copied verbatim from
    `mommy_poppins.py`, checked BEFORE the city map; `_CITY_BOROUGH` string
    fallback covers borough names, common misspellings ("Manhatten"), and
    Queens neighborhood-as-city values. No borough → row dropped.
  - **Ages/tags/price:** `_AGE_BAND_RX` parses "(N–M)" from any category
    name (en dash or hyphen), min-of-mins/max-of-maxes;
    `Baby & Toddler`/`Preschoolers`/`Kids (`/`Tweens` prefixes add
    "best for kids" (Teens alone doesn't); `_CATEGORY_TAGS` maps ~25 topical
    names onto the existing tag vocabulary; "family" is unconditional.
    `_tribe.parse_cost` on the free-text `cost`, with a "Free" category
    backstopping an empty cost. NOTE `parse_cost` returns FREE for
    "Included with admission: $17–$30; free for ages 16 and younger" —
    accepted shared-helper behavior, not worth a fork.
  - **No kid-relevance filter** (100% of rows are family-tagged upstream;
    the site is the filter); shared `ADULT_BLOCKLIST`/`ADULT_TITLE_BLOCKLIST`
    + `MEMBERS_ONLY` (title) as the safety net. Upstream `Nightlife`
    category deliberately NOT hard-excluded — their category tagging is
    spray-everything (a family concert carried every age band plus
    Nightlife), and the observed cases are Long Island rows the geo filter
    already drops.
  - **Multi-day/ongoing listings** (exhibitions, attraction runs like The
    BEAST) are re-listed by the server with a per-day `start_date`, so they
    yield one row per day of the window — same per-occurrence model as
    nycgovparks' daily programs; not a bug, don't "fix" the dedupe.
  - **Fixture:** `tests/fixtures/new_york_family_sample.json` — 8 real rows
    (Brooklyn/Manhattan/Bronx/Queens keeps, Huntington Station + East Meadow
    drops, the shared-id recurring Caterpillar row, an age-band row) + 1
    real page-2 husk. 18 tests in `tests/test_new_york_family_parse.py`.
  - **Fragility watch:** if this source goes quiet, re-probe the API shape
    FIRST (`?start_date=` honored? still 16-cap? envelope back?) — it
    changed once in the six days before the build and will change again.
    The drift alarm (ingest exit 4) is the expected first symptom.

### Brooklyn Botanic Garden (BBG) — ✅ BUILT 2026-07-13

- **Status:** ✅ **BUILT 2026-07-13** — shipped as source `bbg` (httpx +
  selectolax month-page scrape). As-built notes — two structure facts the
  7-06 research didn't have:
  - **The `<h2>` date header is the ul's FIRST CHILD**, not a sibling:
    `<ul id="event-calendar-regular"><h2>Sunday, July 12, 2026</h2><li>…`.
    One ul per calendar day (the id repeats — invalid HTML, selectolax
    doesn't care); the first block is header "Ongoing" (undated exhibit
    runs — skipped). A recurring drop-in program gets a card under EACH
    date it runs, so the h2 date is the occurrence date and
    `external_id = f"{url-slug}:{date}"`.
  - **Full category vocabulary enumerated** (14 labels, pipe-joined
    variants): the family labels are "Families & Kids" and "Children's
    Garden Classes" (curly-apostrophe variant exists) — category ALLOWLIST
    on those two; everything else (continuing-ed, Member Events, Evenings,
    Tours, Exhibits) is adult programming. ~12 family occurrences/month,
    28 in a live 60-day dry run.
  - Time comes from the card's `event-date` prose via a meridiem-required
    regex ("9 a.m.–1 p.m."; date-range numbers can't false-positive); end
    times populate `end_dt`. Month walk = every month page overlapping
    [today, today+60d] (2–3 requests). No price on cards → UNKNOWN.
    `SOURCE_NEIGHBORHOOD` = "Prospect Heights". Opted into
    missing-detection.
- **Original research (2026-07-06):** CONFIRMED (live probe). Real, clean, server-rendered
  calendar — no JSON API, but a stable HTML structure to scrape (BAT-style,
  not Tribe-style).
- **Source:** `https://www.bbg.org/visit/calendar` — custom CMS (not
  WordPress; no Tribe/wp-json routes, no JSON-LD `Event` blocks). Confirmed
  server-rendered with plain `httpx`, no anti-bot encountered.
- **HTML structure (verified live):** `<ul id="event-calendar-regular">`
  containing `<h2>` date headers ("Wednesday, July 8, 2026") followed by
  `<li>` event cards: `<span class="event-tag">` (a real category label,
  e.g. **"Children's Garden Classes"** — seen live on "Garden Adventures"),
  `<h3>` title, `<p class="event-date">` (semi-structured prose — e.g.
  "Wednesday–Friday for two weeks starting July 8, July 22, or August 5,
  2026 | 9 a.m.–1 p.m." — needs lenient parsing, not a clean ISO field),
  `<p class="event-blurb">` description, wrapping `<a href>` for the URL.
- **Filtering plan:** the `event-tag` category is a real, venue-provided
  signal — gate on category (e.g. "Children's Garden Classes" and similar
  family-labeled tags) rather than keyword-guessing the description, same
  spirit as Prospect Park's category allowlist. Confirm the full tag
  vocabulary during the build (only one tag observed in the probe).
- **Borough/venue:** Brooklyn; single fixed venue "Brooklyn Botanic Garden",
  Prospect Heights (adjacent to/shares neighborhood with Prospect Park) →
  `SOURCE_NEIGHBORHOOD` constant.
- **Next step:** `source-verifier` to capture a full fixture and enumerate
  the category vocabulary, then `source-adder` (selectolax parse, same shape
  as Brooklyn Army Terminal).

### Bronx Zoo (+ sibling WCS zoos/aquarium) — 🔴 REJECTED 2026-07-13 (yield)

- **Status:** ❌ **REJECTED 2026-07-13** — the 5-host yield check the 7-06
  probe called for came back far under the bar: **3 items combined across
  all 5 sites** (Bronx Zoo 2, NY Aquarium 1, Central Park/Prospect Park/
  Queens Zoo **0 each**), and all three are season-run spotlights ("May 22 -
  September 7"), not dated occurrences — they don't fit the event model
  without inventing start times. Markup shape re-confirmed (li.postcard),
  so the scraper would be trivial; it's the content that isn't there.
  **Revisit if:** a WCS site ever grows a real dated-events calendar
  (holiday-season lights events are the likeliest trigger — re-probe in
  November), or if season-run "exhibit" rows become desirable catalog
  content (they'd need a synthetic-date convention first).
- **Original research (2026-07-06):** CONFIRMED (live probe). Real content, but sparse —
  and a genuine multi-site bonus if built.
- **Source:** `https://bronxzoo.com/things-to-do/events` — WCS (Wildlife
  Conservation Society) site. Server-rendered `<li class="postcard">` cards:
  `<h4>` title, `<p class="type-caption">` date-range prose ("May 22 -
  September 7" — a season/exhibit run, not a single dated occurrence),
  `<p>` description, card-wrapping `<a href>`. Images load from `cdn.wcs.org`;
  a Contentful CMS reference appears once in the page, but (unlike the
  Brooklyn Cyclones promotions page) the listing itself is plain-HTML
  server-rendered — no JS rendering needed here.
- **Density is low:** only **2 items** on the live page (2026-07-06):
  "Daniel Tiger's Neighborhood at the Bronx Zoo" (a PBS-Kids-branded seasonal
  experience, included with admission) and "Soccer, Summer, and Wildlife".
  This reads as a seasonal-exhibit spotlight list, not a recurring daily
  events calendar — expect low row counts even at full build.
- **Bonus finding — same route works on all 4 sibling WCS NYC facilities:**
  `centralparkzoo.com`, `prospectparkzoo.com`, `queenszoo.com`, and
  `nyaquarium.com` all confirmed live at the identical
  `/things-to-do/events` path with the same markup shape. **One scraper
  class, subclassed 5 ways** (or parameterized by host), would cover Bronx
  Zoo + Central Park Zoo + Prospect Park Zoo + Queens Zoo + NY Aquarium —
  a real coverage multiplier for Queens/Manhattan/Brooklyn/Bronx at once,
  the same "one build unlocks many" shape as NYPL for library branches.
  **Combined yield across all 5 needs checking before committing** — if each
  site independently runs ~2 items, the total build may still be a small
  source, but it's the cheapest possible 5-venue add if so.
- **Filtering plan:** likely no filter needed at this density/curation level
  (WCS picks what to feature) — confirm during the build that nothing
  adult-only slips in (member preview nights, After Hours events, etc., if
  any of the 5 sites surface them).
- **Next step:** `source-verifier` against all 5 hosts to get a real combined
  count before deciding whether this is worth building as one small source.

### New York Botanical Garden (NYBG) — CANDIDATE, dead end on the obvious path

- **Status:** CANDIDATE — probed 2026-07-06, **inconclusive**. The obvious
  approach (WordPress REST API) is a confirmed dead end; a real events feed
  may still exist elsewhere on the site.
- **What the probe found:** NYBG runs WordPress (`wp-json` present, 196
  routes enumerated) but **no Tribe/Events-Calendar routes exist** — checked
  the full route list, nothing event- or calendar-related. `/events/`
  redirects to the NYBG homepage and is a marketing/"Featured Programs" page
  (Flower Power, seasonal exhibits) with **no per-event dated listing**, not
  a calendar. 9 JSON-LD blocks on the homepage are all `Organization`/
  `LocalBusiness` — no `Event` type.
- **Don't repeat:** the `wp-json` route enumeration and the homepage
  JSON-LD check — both dead ends, already done.
- **Where to look next:** NYBG almost certainly runs family programs
  (workshops, camps, Wonder Wheel-style seasonal features) through a
  **separate ticketing subdomain** (common pattern for major
  gardens/museums — c.f. Tessitura at BAM, AudienceView, etc.) not
  discovered in this probe. Check for a `tickets.nybg.org` /
  `calendar.nybg.org` style subdomain, or search the rendered "Featured
  Programs" page for an outbound ticketing link and follow it.
- **Next step:** a fresh probe specifically hunting for the ticketing
  subdomain, not another pass at the marketing site.

### Snug Harbor Cultural Center & Botanical Garden — ✅ BUILT 2026-07-13

- **Status:** ✅ **BUILT 2026-07-13** — shipped as source `snug_harbor`
  (httpx WP REST list + per-detail-page JSON-LD date crawl). Real Staten
  Island coverage — the catalog's thinnest borough (only `si_childrens_museum`
  shared this campus before). As-built notes — the 7-06 "inconclusive, no
  platform signature" verdict was wrong on both counts (the site IS standard
  WordPress with a clean REST API; the earlier probe just didn't find the
  custom `event` post type):
  - **Platform: WordPress custom `event` post type on the standard WP REST
    API** (`/wp-json/wp/v2/event`), NOT Tribe/MEC. Rich taxonomies
    (`audience`, `cost-tier`, `genre`, `program`, `venue`), 160 total events.
  - **THE load-bearing quirk — no event date in the REST payload.** `acf` is
    empty and the post `date` is the creation date; the real event date lives
    ONLY on the detail page in a Yoast JSON-LD `Event` node
    (`startDate`/`endDate`, correct -04:00/-05:00 offset). So, like
    `mommy_poppins`, we fetch the list cheaply then crawl each event's detail
    page for its date. The list is newest-created-first and accumulates past
    events (no server-side date filter possible), so every youth/family event
    is fetched and window-filtered by its JSON-LD date. **~147 detail fetches
    / run** at a 0.5s delay (~1.5 min) — the source's whole cost.
  - **Kid filter = the `audience` taxonomy, resolved by NAME.** Resolve the
    audience terms once/run, query the `event` endpoint for the union of
    {Kids, Families, All Ages, Teens} ids (name-resolution survives a term-id
    renumber). Shared `ADULT_BLOCKLIST`/`ADULT_TITLE_BLOCKLIST`/`MEMBERS_ONLY`
    kept as a title-scope safety net (Snug Harbor is a mixed cultural center,
    not a pure kids feed — occasional 21+ galas carry a family audience tag).
    Teens-only events are kept but don't earn the "best for kids" tag
    (mirrors `new_york_family`).
  - **Every taxonomy resolved id->name once/run** (the `brooklyn_bridge_park`
    location-resolution shape). `cost-tier` -> price (Free -> FREE; the
    $10/$25/$50/Above-$50 tiers -> PAID; "Pay What You Wish"/"#N/A"/none ->
    UNKNOWN). `genre`/`program` -> tags. Term names carry HTML entities
    (`$10 &amp; Under`), unescaped before use.
  - Venue/borough hardcoded (single campus) → `SOURCE_NEIGHBORHOOD
    ["snug_harbor"] = "Snug Harbor"` (same Livingston/New Brighton campus as
    `si_childrens_museum`). The per-event `venue` taxonomy (Chinese Scholar's
    Garden, Great Hall, Heritage Farm, …) is a spot within the campus — kept
    in `raw_payload`, not used as the venue name. `external_id` = WP post id
    (recurring programs are separate posts; no per-day expansion).
    `window_days = 60` → opted into missing-detection.
  - **Fixture:** `tests/fixtures/snug_harbor_sample.json` — a `terms` block
    (the resolved taxonomy id->name maps) + 12 real `{item, jsonld}` rows
    (mix of Free/PWYW/$10 cost tiers, Families/Teens-only audiences, in- and
    out-of-window dates). 22 tests in `tests/test_snug_harbor_parse.py`; the
    parser (`parse_event`) is pure and takes the REST item dict + the JSON-LD
    dict, so no httpx mock. Verified live end-to-end (147 events listed, real
    Staten Island rows yielded).
- **Original research (2026-07-06):** CANDIDATE — probed, **inconclusive**.
  No platform signature found on `/events-calendar/`; that probe grepped for
  known plugin tells and missed the custom `event` post type on the plain WP
  REST API. Lesson: when a WordPress site shows no Tribe/MEC tell, enumerate
  `/wp-json` for custom event post types before concluding "no feed."

### Bronx River Alliance — CANDIDATE, thin/low-priority

- **Status:** CANDIDATE — probed 2026-07-06, **looks thin**. Deprioritize
  relative to the other finds in this batch.
- **What the probe found:** WordPress + Elementor site, no Tribe/events
  plugin (`wp-json` route list has no event/calendar routes). The events
  page (`/visit-the-river/calendar`, redirect target of `/calendar/`) renders
  almost no content in static HTML — just a page header and a volunteer
  interest form, no visible event listing at all in the fetched markup.
  Either the listing is a JS-rendered widget invisible to a plain fetch, or
  this org simply doesn't run a structured events calendar (announcements
  may be newsletter/social-only).
- **Next step:** low priority given the other finds in this batch; if
  revisited, check with a real browser render before concluding either way.

### Macaroni Kid (Brooklyn NW + Lower Manhattan) — CANDIDATE, platform identified but access blocked

- **Status:** CANDIDATE — probed 2026-07-06. **Platform identified** (a real
  find), but the actual data endpoint is bot-protected and this session
  couldn't get past it — needs a retry, not a rejection.
- **What it is:** Macaroni Kid is a nationwide network of hyperlocal
  parenting-newsletter franchises; NYC has multiple neighborhood editions
  (this request named `brooklynnw` and `lowermanhattan` — others likely
  exist for other neighborhoods, unconfirmed).
- **Platform confirmed:** every Macaroni Kid site embeds a third-party
  widget from **Yodel** (`events.yodel.today`) via
  `<script data-src="https://events.yodel.today/y/widget/<per-site-id>">` —
  e.g. `brooklynnw` → `69cd3f6c94f9f559cc38ba27`, `lowermanhattan` →
  `69cd3f4f94f9f559cc38b9f3`. Each neighborhood site has its own widget id,
  so this is a **franchise-network platform, same shape as NYPL/QPL or the
  Tribe sources** — cracking the Yodel widget API once likely means every
  other Macaroni Kid NYC neighborhood is a cheap copy-adapt.
  ⚠️ **Do not confuse this with `assets.apollo.io`** — an unrelated
  sales-tracking pixel that also appears on these pages; it is not a
  GraphQL/Apollo client and is not the data source.
- **Re-probe 2026-07-13 (off-proxy, `curl_cffi impersonate="chrome"`):
  Cloudflare falls and the widget page server-renders the data.**
  `GET events.yodel.today/y/widget/69cd3f6c94f9f559cc38ba27` (brooklynnw) →
  200, 645 KB, with a **JSON-LD `ItemList` of 24 `Event` nodes**: name,
  startDate/endDate (UTC — convert to America/New_York), full
  `location.address.streetAddress` **including ZIP** (nice for geocoding),
  image, organizer, and a per-event `events.yodel.today/y/event/...` URL.
  Open build questions: whether 24 items is the full forward window or one
  page (the widget references a `my.yodel.today/api/v3/` host — a richer
  JSON API may exist behind it), and **dedup risk is real**: the probe's
  second item was a BPL Central Library storytime we already ingest via
  `bpl`. Kid filter: unclear the widget is kid-only — sample skewed family
  but verify. Worth building only after the bigger confirmed sources; when
  built, cross-check overlap against `bpl`/`mommy_poppins` first.

### Brooklyn Academy of Music (BAM)

- **Status:** CANDIDATE — proposed 2026-06-27. **Re-probed 2026-07-13 (web
  sandbox): reachable but JS-rendered.** `bam.org/` returns 200 (360 KB) and
  `/calendar` just anchors to `#Calendar` on the homepage — a client-side SPA
  with no `wp-json`/Tribe route and no JSON-LD `Event` in the static HTML
  (`/api/search` 404s). Likely a **headless-browser candidate** unless a
  Tessitura/EPS JSON endpoint turns up. Next session: probe for an embedded
  JSON blob or a `*.bam.org`/Tessitura events API before reaching for
  Playwright.
- **Why:** BAM runs a dedicated family strand — **BAMkids** (BAMkids Film
  Festival, family matinees, workshops) — so there's a real kid-relevant
  subset, unlike an all-adult performing-arts calendar.
- **URLs to probe:** `https://www.bam.org/programs/bamkids` and the main
  calendar `https://www.bam.org/programs` / `https://www.bam.org/calendar`.
- **Platform guess (verify, don't trust):** BAM ticketing has historically run
  on **Tessitura** (and the probe snippet already greps for `tessitura`); the
  marketing site may be a separate CMS. Tessitura usually exposes a JSON
  "TNEW"/EPS API but often behind auth — check for a public `/api` or embedded
  JSON-LD `Event` blocks on the listing pages first. Expect anti-bot 403s →
  `curl_cffi impersonate="chrome"`.
- **Filtering plan if built:** this is a curated *venue*, not a kids feed, so it
  needs a kid-relevance gate — restrict to the BAMkids category if the feed
  exposes categories, else title/description keyword inclusion (the
  Industry-City strategy). Hard-exclude the adult mainstage (opera, late-night,
  21+). Single venue → `SOURCE_NEIGHBORHOOD["bam"] = "Fort Greene"` for the
  neighborhood pass (BAM is in Fort Greene / the BAM Cultural District).
- **Borough/venue:** Brooklyn; venue "Brooklyn Academy of Music" (multiple
  buildings — Howard Gilman Opera House, Harvey Theater, BAM Rose Cinemas — all
  Fort Greene, so one neighborhood label is fine).
- **Open question:** does BAMkids carry enough *dated, non-film* events to be
  worth a source, or is it mostly the annual film festival? Gauge yield during
  the probe before committing to `source-adder`.

### Puppetworks

- **Status:** CANDIDATE — proposed 2026-07-05, unprobed (sandbox egress to
  `puppetworks.org` was reset/blocked this session — `curl_cffi
  impersonate="chrome"` got `Recv failure: Connection reset by peer` on `/`,
  `/calendar`, `/schedule`, `/tickets`, `/events`; retry from a different
  network before concluding it's actually unreachable, per the "sandbox
  egress varies" note above).
- **Why:** dedicated marionette/puppet theater — all-ages by construction,
  no kid-relevance filter likely needed (same "curated kids feed" bucket as
  `mommy_poppins`/`bk_childrens_museum`).
- **URLs to probe:** `https://puppetworks.org` plus a calendar/schedule/
  tickets page (exact path unconfirmed — probe blocked before a page loaded).
  Check for a ticketing-platform embed (many small theaters run Eventbrite,
  Ticketleap, or a custom WordPress calendar) — grep for the usual tells
  (`wp-json`, `tribe-events`, `eventbrite`, JSON-LD `Event`).
- **Borough/venue:** proposed as Brooklyn Bridge Park — **verify during the
  probe**, since Puppetworks has historically been sited in Park Slope
  (338 6th Ave), not Brooklyn Bridge Park; confirm the current address before
  hardcoding a `SOURCE_NEIGHBORHOOD` entry.
- **Filtering plan if built:** likely no filter needed (single-purpose kids'
  venue) — confirm the full program list is actually all-ages before skipping
  a filter, same caution as the other curated feeds.

### Brooklyn Bridge Park — ✅ BUILT 2026-07-13

- **Status:** ✅ **BUILT 2026-07-13** — shipped as source
  `brooklyn_bridge_park`. Probed same-session as the WCS rejection (this
  environment's egress reached it fine) and built as the batch's third
  source. As-built notes — the platform guess below was HALF right:
  - **WordPress yes, Tribe NO.** The custom `events` post type is exposed
    on the standard WP REST API (`/wp-json/wp/v2/events?per_page=100`,
    671 posts / 7 pages) with ACF fields: `date` (YYYYMMDD local),
    `start_time`/`end_time` ("H:MM am/pm" wall times), `recurring_event`
    + `select_date_&_time` occurrence array, `event_location` (references
    the `maplocations` post type → per-pier venue names, resolved once per
    run), `description` (HTML). `event_category` taxonomy has NO kids
    term — see filter below.
  - **THE load-bearing quirk — recurring parents AND dated posts overlap:**
    the same program is posted both as a recurring parent (occurrence
    array) and as per-date posts titled "<Program> – July 14", covering
    the SAME dates. `parse_posts` dedups on (dated-suffix-stripped title,
    date), preferring the dated post (occurrence-specific URL). Without
    this, rows double-count.
  - **Filter (inclusive + blocklist, title-only scope):** category
    hard-excludes Benefit Events / Socials & Dancing / Volunteer; Fitness
    excluded unless the title has a family signal (family/kids/youth/
    toddler/stroller/teen — keeps "Family Kayaking", drops "Sunset Yoga");
    shared adult blocklists on the TITLE ONLY — body text carries
    registration fine print ("parent/guardian who is 18+ must register"
    appears on Pokémon Day Out), so body-scope matching drops exactly the
    wrong events. Uncategorized rows pass (Storytime with BPL is
    uncategorized).
  - **Yield:** 139 events in a live 60-day dry run. Price hardcoded FREE
    (the park's free public programming — BAT precedent).
    `external_id = f"{post_id}:{date}"`. Opted into missing-detection
    (full-collection re-fetch each run). `SOURCE_NEIGHBORHOOD` =
    "Brooklyn Bridge Park" (park-name-as-neighborhood, the Prospect Park
    precedent; per-pier NTA splitting deferred).
- **Original research (2026-07-05):** CANDIDATE, unprobed (sandbox egress to
  `brooklynbridgepark.org` was reset/blocked that session).
- **Why:** a major waterfront park with a large recurring family-program
  calendar (free movies, kayaking, playgrounds programming, seasonal
  festivals) — a real Phase-2-shaped venue source, similar in spirit to
  Prospect Park / Domino Park / Governors Island.
- **URLs to probe:** `https://www.brooklynbridgepark.org/events` and
  `/calendar` (exact path unconfirmed — probe blocked before a page loaded).
  Grep for the usual platform tells (`wp-json`/`tribe-events`, Squarespace,
  Sanity, JSON-LD `Event`, embedded JSON) once reachable; a nonprofit park
  conservancy site is plausibly WordPress/Tribe (same stack as Green-Wood/
  Prospect Park/NY Transit) or a custom CMS — don't assume, probe.
- **Filtering plan if built:** curated park site, likely inclusive-with-
  blocklist like Prospect Park/Domino/Governors Island (adult-only events are
  the minority) — confirm strategy once the category/tag shape is known.
- **Borough/venue:** Brooklyn; the park spans multiple piers/sections
  (DUMBO through Cobble Hill) — check whether events carry a per-pier
  location that would need `VENUE_NEIGHBORHOOD` (like NY Transit's two
  sites / the Met's two buildings) rather than a single
  `SOURCE_NEIGHBORHOOD` constant, since DUMBO and Cobble Hill are different
  NTAs.
- **Note:** Puppetworks (added above, same session) was proposed as sited
  "in Brooklyn Bridge Park" — Puppetworks is actually a separate, historically
  Park-Slope-based venue, not part of the park itself. Treat these as two
  distinct candidates; don't conflate their venues/neighborhoods if both get built.

### Brooklyn Bridge Parents — brooklynbridgeparents.com

- **Status:** CANDIDATE — proposed 2026-07-07, unprobed (single homepage
  fetch only; no endpoint/platform probe run yet).
- **Not to be confused with** the "Brooklyn Bridge Park" entry above
  (`brooklynbridgepark.org`) — that's the physical waterfront park's own
  event calendar; this is a separate Brooklyn-focused parenting magazine/
  directory site, closer in kind to the New York Family entry below than
  to a single-venue source.
- **Why:** Brooklyn-focused family content site with a dedicated events
  section, school guides, and camps/after-school listings. Brooklyn-only
  scope would sidestep New York Family's regional (Long Island-bleeding)
  geo-filter problem, if the feed holds up.
- **Site type:** WordPress (`/wp-content/` paths visible on fetch); a
  hybrid blog + events calendar + local-business directory ("CONNECT").
  Not a single-purpose event calendar — most of the site is unrelated
  content (restaurants, real estate, school guides), so whatever feed
  probing finds will need real filtering, not a bare pass-through.
- **URLs to probe:** `https://brooklynbridgeparents.com/events/` (the
  events listing). Check for a Tribe Events Calendar REST endpoint first
  (`/wp-json/tribe/events/v1/events`) — five sources already built on that
  plugin, worth ruling in/out before assuming a custom scrape is needed.
- **Caution — user-submitted events:** the site has a public
  `/post-an-event/` submission form and an `/event-dashboard/` — events
  look user/business-submitted, not editorially curated like Mommy
  Poppins/BPL. Expect more promotional noise and inconsistent quality than
  the curated sources; may need a stricter filter than the "inclusive +
  blocklist" sources use.
- **Next step:** `source-verifier` — confirm the Tribe endpoint (or
  identify the real platform if it's not Tribe), sample real event rows,
  and assess submission-noise levels before committing to `source-adder`.

### NYC public libraries — system map (read before building any of the four below)

NYC has **three** public-library systems, not five:

- **Brooklyn Public Library (BPL)** — Brooklyn. **BUILT** (source `bpl`).
- **Queens Public Library (QPL)** — Queens.
- **New York Public Library (NYPL)** — **Manhattan, the Bronx, AND Staten
  Island.** There is no separate "Bronx Public Library" or "Staten Island
  Public Library"; those branches are NYPL.

So the "Bronx Library" and "Staten Island Library" items below are **borough
slices of NYPL**, tracked separately at the maintainer's request — building the
single NYPL source satisfies all three (filter by branch borough if per-borough
tracking is wanted).

**Neighborhood coding is already done for all of these.** `library_neighborhoods.json`
was built NYC-wide from FacDB and is borough-keyed: it already holds Queens (67),
Manhattan (42), Bronx (35), and Staten Island (14) branch keys. So the enrich
pass codes a QPL/NYPL branch the moment a source yields it — **no new data-prep**.
The one requirement: the source must set each event's **correct branch borough**
(NYPL spans three, so it can't hardcode one), since the library lookup is
keyed `"<borough>|<library-core>"`.

### Queens Public Library (QPL)

- **Status:** 🟢 **CONFIRMED — re-probed 2026-07-13 off-proxy;
  `curl_cffi impersonate="chrome"` gets past the F5/BIG-IP wall and the
  calendar is server-rendered.** Platform: Drupal 10 + a custom Solr search
  front (`qbpl_solr` / `qbpl_events` modules) — NOT LibCal/Communico.
  Load-bearing probe facts:
  - Bare `/calendar` silently serves the **homepage** — the listing needs the
    nav link's full query string:
    `/calendar?searchField=%2A&category=calendar&fromlink=calendar&searchFilter=`
    (title "Calendar", 12 server-rendered result cards).
  - Pagination is the hidden-iframe endpoint
    `/search/call?searchField=%2A&category=calendar&pageParam=<n>&searchFilter=`
    — same card HTML, 12 cards/page (verified page 2). Facet filters ride
    `searchFilter=` (Solr syntax); simpler to fetch unfiltered and gate in
    the parser.
  - Each card carries an **audience line** (`<p class="category">`: "For
    Kids(0-5)", "For Adults", "Family Literacy", "Gaming", …), title, a
    "Jul 17, 1:30pm - 3:00pm" date line, branch (`<p class="location">`),
    truncated description, and a `/calendar/<slug>/<id>` detail link. An
    inline `arrCal['<id>']` JS blob holds every occurrence timestamp for
    recurring events (parseable for exact datetimes).
  - Detail pages embed `drupalSettings.eventCalendar` = `{id, title,
    description, calendar_start, calendar_end, branch}` — clean JSON, no
    JSON-LD needed. Event ids look like `012497-0226` (id-monthyear).
  Kid gate: parse the card audience line (keep the Kids/Families/Teens
  buckets). Neighborhood coding is already done (67 Queens branch keys in
  `library_neighborhoods.json`). Fixture capture must be off-proxy.
- **System:** Queens only (~65 branches). Canonical domain **queenslibrary.org**
  (NOT `queenspubliclibrary.org` — that domain currently redirects to a junk
  site; don't probe it).
- **Platform (confirmed 2026-07-13):** Drupal 10 + custom Solr front — see
  the Status bullet for the endpoints; no LibCal/Communico/BiblioCommons.
- **Filtering plan:** gate on the card audience line (Kids/Families/Teens
  buckets in; adult/senior-only out).
- **Borough/venue:** Queens; venue = branch name (so neighborhood coding via the
  library table works); borough always Queens.

### New York Public Library (NYPL) — ✅ BUILT

- **Status:** ✅ **BUILT 2026-07-13** as source `nypl`. As-built: scrapes the
  server-rendered Drupal listing **per borough** via the site's own `city[]`
  filter (`bx`/`man`/`si`) — so borough comes free with no detail crawl and
  no branch→borough mapping. `date_op=GREATER_EQUAL&date1=today` makes the
  listing ascending-by-occurrence so pagination bounds cleanly on the window.
  **The server-side `audience` filter is loose (adult rows come back), so the
  real kid gate is the client-side audience-cell token check.** Occurrence
  date/time come from the "Today @ 2 PM" cell (the URL-path date is the
  event's canonical date and is WRONG for recurring programs);
  `external_id = url:start_iso` because the URL repeats across occurrences and
  the audience-union duplicates rows (deduped within the fetch too). Age range
  parsed from "ages 6-12" phrasing; price FREE; virtual ("Online") rows
  dropped; venue = branch name → neighborhood via `library_neighborhoods.json`.
  **⚠️ HIGH VOLUME: thousands of events over the 60-day window (NYPL runs
  daily programming across ~88 branches) — by far the largest source. The
  `window_days` knob caps it if the maintainer wants fewer.** Module
  `sources/nypl.py` + fixture `tests/fixtures/nypl_calendar_kids_page.html` +
  24 parser tests. This source unlocks the Bronx + Staten Island library
  items below. Original confirmation notes below.
- **Confirmed — re-probed 2026-07-13 off-proxy;
  `curl_cffi impersonate="chrome"` sails past Incapsula and
  `/events/calendar` is a server-rendered Drupal table.** Probe facts:
  - ~42–48 event rows per page, paginated `?page=N`. Depth check: still 40+
    rows at page 30, so the catalog is deep — bound the nightly walk by the
    ingest window, not by walking to the end.
  - Each **listing row** already carries date/time, title, **full
    description**, branch (`event-location` cell), and an **Audience cell**
    ("Children", "Infant (0-18 months)", "Toddlers (18-36 months)",
    "Pre-schoolers (3-5 years)", "Families", "Parents/Caregivers", …) — the
    kid-relevance gate is a taxonomy read, not keyword guessing.
  - **Detail pages embed a complete JSON-LD `Event`** (startDate/endDate with
    TZ offset, `location.name` = branch, `location.address.addressLocality` =
    the borough — so the per-branch borough requirement is satisfied from the
    detail page; alternatively invert the borough-keyed library table from
    the branch name and skip the detail fetch).
  - The exposed date filter (`date_op`/`date1` selects) did **not** filter in
    the probe (params likely spelled differently) — either crack the real
    param during the build or paginate with a page cap + window check.
  Neighborhood coding is already done (35 Bronx + 42 Manhattan + 14 Staten
  Island branch keys in `library_neighborhoods.json`); **this one source
  unlocks the Bronx + Staten Island library items below.** Remember the borough
  MUST be set per-branch (NYPL spans three) or the borough-keyed library lookup
  misses. Fixture capture must be off-proxy.
- **System:** **Manhattan + Bronx + Staten Island** (~90 branch libraries plus
  the research libraries). Building this one source is what actually unlocks the
  Bronx and Staten Island items below.
- **Platform (confirmed 2026-07-13):** Drupal, server-rendered listing table
  at `/events/calendar` + JSON-LD `Event` on detail pages — see the Status
  bullet. No headless browser needed.
- **Filtering plan:** gate on the listing Audience cell (Children / Infant /
  Toddlers / Pre-schoolers / Families / school-age in; adult-only and
  research-library lectures out).
- **Borough/venue — IMPORTANT:** NYPL spans three boroughs, so the source MUST
  set each event's borough from its branch (not a hardcoded constant), or the
  borough-keyed library neighborhood lookup will miss. venue = branch name.

### Bronx Library (NYPL — Bronx branches)

- **Status:** CANDIDATE — proposed 2026-06-27. **Not a separate system** — these
  are NYPL's Bronx branches (~35 in FacDB). Tracked separately per request.
- **Build path:** covered by the NYPL source above; no distinct endpoint. If
  per-borough delivery is wanted, filter the NYPL feed to `borough == Bronx`.
- **Neighborhood coding:** already covered (35 Bronx library keys in the table).

### Staten Island Library (NYPL — Staten Island branches)

- **Status:** CANDIDATE — proposed 2026-06-27. **Not a separate system** — these
  are NYPL's Staten Island branches (~13 in FacDB). Tracked separately per request.
- **Build path:** covered by the NYPL source above; no distinct endpoint. If
  per-borough delivery is wanted, filter the NYPL feed to `borough == Staten Island`.
- **Neighborhood coding:** already covered (14 Staten Island library keys in the table).

### NYC art museums — Manhattan (read before building any of the three below)

Three flagship Manhattan art museums proposed 2026-06-28. All are **curated,
adult-skewing venues**, not kids feeds, so each needs a kid-relevance gate to its
family/kids strand (the BAM strategy: category filter if the calendar exposes
one, else title/description keyword inclusion). All are single fixed venues → a
`SOURCE_NEIGHBORHOOD` constant each, **except the Met** (two buildings in
different neighborhoods → handle like NY Transit's two sites via
`VENUE_NEIGHBORHOOD`). Expect anti-bot 403s on these consumer sites → probe with
`curl_cffi impersonate="chrome"`. None is confirmed to have a structured feed;
if a probe finds the calendar is JS-only with no JSON-LD / embedded JSON / JSON
endpoint, it's a **headless-browser candidate** (Phase-3 Playwright fallback).
Probe one first to learn the platform shape; copy-adapt if the others match.

**Re-probe 2026-07-13 (off-proxy, `curl_cffi impersonate="chrome"`): the
walls fall, but no cheap parse surfaced.** The Met's `/events` (and
`/en/events`) returns 200 but is a **Next.js App-Router SPA** — zero event
links in the HTML, no JSON-LD, no `__NEXT_DATA__`; the event data rides the
React Server Components flight payload (`self.__next_f.push(...)` chunks, 20
`startDate` hits) — a fragile custom parse of an undocumented wire format.
**Deprioritized: reachable but effectively headless-tier** unless a JSON
endpoint turns up in browser devtools. MoMA and the Whitney below remain
unprobed (expect similar stacks; probe before writing any code).

### The Metropolitan Museum of Art (The Met)

- **Status:** CANDIDATE, deprioritized — probed 2026-07-13 off-proxy: Next.js
  App-Router SPA, event data only in the RSC flight payload (see the block
  note above). Effectively headless-tier.
- **Why:** the Met runs a substantial family strand — **#MetKids**, family
  programs, drop-in drawing, story time, workshops — a real kid-relevant subset
  under an otherwise adult calendar.
- **URLs to probe:** `https://www.metmuseum.org/events` (filterable by audience —
  look for a "Families"/"Kids and Families" filter and whether it maps to a query
  param) and the MetKids landing page.
- **Platform guess (verify, don't trust):** large custom CMS (not WordPress/
  Tribe). Check listing/detail pages for JSON-LD `Event` blocks, a
  `__NEXT_DATA__`/embedded-JSON blob, or an events JSON endpoint under
  `metmuseum.org`. **Note:** the well-known Met "Open Access" API
  (`collectionapi.metmuseum.org`) is the *art collection*, NOT events — don't
  confuse them.
- **Filtering plan if built:** gate to the family/kids audience by filter/category
  if exposed, else keyword inclusion (story time, family, kids, workshop,
  drop-in). Hard-exclude adult programming (members' openings, lectures, galas,
  21+ evening events).
- **Borough/venue — TWO sites:** Manhattan. Main building = Fifth Ave at 82nd
  (Upper East Side / Museum Mile); **The Met Cloisters** = Fort Tryon Park,
  Washington Heights. If both carry events, set venue per-event and code
  neighborhood via `VENUE_NEIGHBORHOOD` (Met Fifth Ave → Upper East Side; Met
  Cloisters → Washington Heights) — the NY-Transit two-site pattern, not a single
  `SOURCE_NEIGHBORHOOD` constant.
- **Open question:** does the family strand carry enough *dated* events (vs.
  always-on gallery activities) to be worth a source? Gauge yield in the probe.

### Museum of Modern Art (MoMA)

- **Status:** CANDIDATE — proposed 2026-06-28, unprobed.
- **Why:** MoMA's family programs (Art Lab, family gallery sessions, "Tours for
  Fours", workshops) are a defined kid-relevant subset.
- **URLs to probe:** `https://www.moma.org/calendar/` (and the family/kids filter
  if one exists). Check **MoMA PS1** (`https://www.momaps1.org/`) separately — a
  distinct Queens venue with its own calendar — only if PS1 runs family events.
- **Platform guess (verify):** custom CMS/React. Grep for JSON-LD `Event`,
  embedded JSON (`__NEXT_DATA__`/Apollo state), or a calendar JSON endpoint.
  Headless fallback if JS-only.
- **Filtering plan if built:** gate to family/kids programs; hard-exclude members'
  previews, adult film series, evening adult events.
- **Borough/venue:** Manhattan; venue "Museum of Modern Art", 11 W 53rd St →
  `SOURCE_NEIGHBORHOOD["moma"]` = Midtown. **MoMA PS1, if included, is Long Island
  City, QUEENS** — different borough + neighborhood, so treat PS1 as a separate
  venue/source rather than hardcoding one borough.

### Whitney Museum of American Art (The Whitney)

- **Status:** CANDIDATE — proposed 2026-06-28, unprobed.
- **Why:** the Whitney runs family days, kids/teen workshops, and "Open Studio"
  drop-ins — a kid-relevant strand under an adult contemporary-art calendar.
- **URLs to probe:** `https://whitney.org/events` (look for an audience/family
  filter and its query param).
- **Platform guess (verify):** custom CMS. Check for JSON-LD `Event`, embedded
  JSON, or an events JSON endpoint; headless fallback if JS-only. Expect a
  possible anti-bot 403 → `curl_cffi`.
- **Filtering plan if built:** gate to family/kids/teen programs by category if
  exposed, else keyword inclusion; hard-exclude members' events, adult talks, 21+
  evenings.
- **Borough/venue:** Manhattan; venue "Whitney Museum of American Art", 99
  Gansevoort St (Meatpacking District) → `SOURCE_NEIGHBORHOOD["whitney"]` = West
  Village (the NTA "West Village" covers the Meatpacking blocks — verify the
  reverse-geocode lands there during the enrich pass).

### Brooklyn Museum

- **Status:** CANDIDATE — proposed 2026-07-06, unprobed.
- **Why:** runs a dedicated family strand (First Saturdays free late-night —
  partly adult but includes family/kids programming earlier in the evening,
  Brooklyn Museum Kids, Great Hall drop-in workshops, Target First Saturdays
  kids' activities) — a real kid-relevant subset under an adult-skewing
  contemporary/fine-art calendar. Don't confuse with **Brooklyn Children's
  Museum** (already BUILT, `bk_childrens_museum`) — this is the separate,
  larger fine-arts museum on Eastern Parkway.
- **URLs to probe:** `https://www.brooklynmuseum.org/calendar` (look for a
  family/kids filter or category) and the First Saturdays landing page.
- **Platform guess (verify, don't trust):** custom CMS. Check for JSON-LD
  `Event` blocks, embedded JSON, or a calendar JSON endpoint on listing/detail
  pages. Expect anti-bot 403 on the consumer site → `curl_cffi
  impersonate="chrome"`. Headless-browser candidate if JS-only.
- **Filtering plan if built:** gate to family/kids programs by category if
  exposed, else keyword inclusion (family, kids, drop-in, Great Hall,
  storytime); hard-exclude 21+ evening programming, members' previews, adult
  talks/lectures. First Saturdays itself is a mixed adult/family event — if
  included, don't drop it wholesale just because it also has an adult DJ set;
  judge by whether the listing itself is family-labeled.
- **Borough/venue:** Brooklyn; venue "Brooklyn Museum", 200 Eastern Parkway →
  `SOURCE_NEIGHBORHOOD["brooklyn_museum"]` = Prospect Heights (verify the NTA
  during the enrich pass — the address sits near the Crown Heights North /
  Prospect Heights border).

### New York Hall of Science (NYSCI)

- **Status:** CANDIDATE — proposed 2026-07-06. **Re-probed 2026-07-13 (web
  sandbox): reachable with plain `httpx` (NOT anti-bot-walled), but the events
  path is unconfirmed and it likely delegates to Eventbrite.** `nysci.org/`
  returns 200 (90 KB) and **embeds an Eventbrite reference**; `nysci.org/events/`
  and `/calendar/` both 404, and there's no `wp-json`/Tribe route. So the
  calendar isn't at the guessed paths — the programming is probably ticketed
  through an **Eventbrite organizer** (or a JS widget on another page).
  **Next step (any session — this host isn't blocked): find NYSCI's Eventbrite
  organizer id (search the homepage/booking links for `eventbrite.com/o/…` or
  an `eventbrite.com/e/…` embed), then hit the public Eventbrite organizer
  events endpoint** — a different build shape than the WordPress/JSON-LD
  sources here, worth scoping before committing. Not blocked by the
  `curl_cffi` sandbox issue.
- **Why:** a hands-on science museum built for kids/families — likely closer
  to the "curated kids feed" bucket (like `mommy_poppins`/`bk_childrens_museum`)
  than a filtered adult calendar, since nearly everything NYSCI runs is
  family-facing. Still worth confirming — camps/member-only sessions may need
  excluding.
- **URLs to probe:** `https://nysci.org/events/` or `/calendar` (exact path
  unconfirmed).
- **Platform guess (verify):** unknown CMS — grep for JSON-LD `Event`,
  `wp-json`/Tribe, Eventbrite embed, or a calendar JSON endpoint. Expect
  possible anti-bot → `curl_cffi impersonate="chrome"`.
- **Filtering plan if built:** confirm whether a filter is even needed (all-ages
  science center) before adding one; if members-only/private-rental events
  appear in the same feed, exclude by category/keyword.
- **Borough/venue:** Queens; venue "New York Hall of Science", Corona
  (Flushing Meadows Corona Park) → likely a `SOURCE_NEIGHBORHOOD` constant
  once the NTA is confirmed (Corona).

### American Museum of Natural History (AMNH)

- **Status:** CANDIDATE, low priority — **re-probed 2026-07-13 off-proxy with
  `curl_cffi impersonate="chrome"`: reachable (200), but thin.** `/calendar`
  is Ibexa CMS (eZ Platform) and server-renders only **~8 featured event
  cards** (`amnh-calendar-new-event` class: sleepovers, member events, a
  moth night) — title/description/date are parseable, but there's no month
  grid in the HTML, no date query param (`?date=2026-08` returns the same 8
  cards), no JSON-LD, and no API URL in the `amnh.js` bundle. The full
  calendar is JS-driven. **Options: ship a low-yield featured-only source
  (~8 rows), or spend a browser-devtools session hunting the XHR the grid
  makes. Deprioritized behind the confirmed batch.**
- **Why:** major family destination — Discovery Room, family workshops,
  Space Show family programming, overnight "Night at the Museum" sleepovers —
  a well-defined kid-relevant strand under an otherwise mixed adult/family
  calendar (member lectures, 21+ evening events like "One Step Beyond").
- **URLs to probe:** `https://www.amnh.org/calendar` (look for a family/kids
  audience filter and its query param).
- **Platform guess (verify):** large custom CMS. Check for JSON-LD `Event`,
  an embedded JSON blob (`__NEXT_DATA__` or similar), or an events JSON
  endpoint under `amnh.org`. Expect anti-bot 403 → `curl_cffi
  impersonate="chrome"`; headless fallback if JS-only.
- **Filtering plan if built:** gate to family/kids programs by
  category/audience filter if exposed, else keyword inclusion (family, kids,
  Discovery Room, sleepover, workshop); hard-exclude adult member events,
  21+ evening programs, fundraising galas.
- **Borough/venue:** Manhattan; venue "American Museum of Natural History",
  Central Park West at 79th St → `SOURCE_NEIGHBORHOOD["amnh"]` = Upper West
  Side.

### Intrepid Sea, Air & Space Museum (USS Intrepid)

- **Status:** 🟢 **CONFIRMED (endpoint verified, params still to map) —
  re-probed 2026-07-13 off-proxy with `curl_cffi impersonate="chrome"`.**
  Real path is **`/events/calendar`** (`/visit/calendar` 404s). It's a
  **Drupal calendar view** (`drupalSettings.views.ajaxViews`: view_name
  `calendar`, display `calendar`, path `/node/68`, better_exposed_filters).
  A plain `POST /views/ajax` with the view identifiers (+ the page's
  `view_dom_id`) returns the rendered rows: `datetime="2026-07-14T09:30:00-04:00"`
  attributes + event detail links (`/free-world-cup-watch-parties-pier-86-0`,
  `/inspiration-academy-…`) and a "Load More" pager. Remaining build
  questions: the exposed-filter param names for a date range, the pager param
  (`page=N` in the POST body), and whether detail pages carry JSON-LD
  (the site emits JSON-LD elsewhere, so likely yes). Yield looked modest
  (a few events/day). Fixture capture must be off-proxy.
- **Why:** family-oriented museum (aircraft carrier, space shuttle pavilion)
  with school-break camps, family days, and STEM workshops — real kid-relevant
  programming distinct from its adult evening-rental/gala business.
- **URLs to probe:** `https://intrepidmuseum.org/visit/calendar` or
  `/events` (exact path unconfirmed).
- **Platform guess (verify):** unknown CMS — grep for JSON-LD `Event`,
  `wp-json`/Tribe, ticketing-platform embeds (Eventbrite/Tessitura), or a
  calendar JSON endpoint. Expect anti-bot → `curl_cffi
  impersonate="chrome"`.
- **Filtering plan if built:** gate to family/kids/STEM programs if a
  category exists, else keyword inclusion; hard-exclude private evening
  rentals, galas, 21+ events.
- **Borough/venue:** Manhattan; venue "Intrepid Museum", Pier 86 (W 46th St)
  → `SOURCE_NEIGHBORHOOD["intrepid"]` = Hell's Kitchen / Clinton (verify NTA
  name during enrich pass).

### City Parks Foundation (cityparksfoundation.org) — ✅ BUILT

- **Status:** ✅ **BUILT 2026-07-13** as source `city_parks_foundation`
  (sixth `TribeEventsSource` subclass). As-built: category allowlist
  `{PuppetMobile, SummerStage}`, ALL SummerStage kept (maintainer call — no
  shared adult blocklist applied, unlike Industry City); borough is
  **per-event from `venue.venue`** (which holds the borough string, not a
  park), `venue_name` left None (no structured park), price from `cost`,
  `is_virtual`/non-borough venues dropped. ~49 events/60d live. Module
  `sources/city_parks_foundation.py` + fixture
  `tests/fixtures/city_parks_foundation_sample.json` + 22 parser tests.
  Original confirmation notes below.
- **Confirmed — re-probed 2026-07-13 off-proxy with
  `curl_cffi impersonate="chrome"`: it's a standard WordPress + The Events
  Calendar (Tribe) site and the REST API is open.**
  `GET /wp-json/tribe/events/v1/events?start_date=…&end_date=…&per_page=50`
  returns clean JSON — **82 events in a 55-day window**, categories include
  **`PuppetMobile`** (the kids strand), **`SummerStage`**, `Volunteer: It's My
  Park`, `Partnerships for Parks`, `Grants and More`. Venue objects carry the
  per-event park (probe sample: venue "Brooklyn") — verify venue/city/borough
  field mapping during the build; citywide multi-borough, so borough must be
  per-event. **This is the fifth Tribe source → subclass `TribeEventsSource`,
  never copy-adapt.** Kid gate (**maintainer call, 2026-07-13**): category
  allowlist with **`PuppetMobile` AND `SummerStage` both included wholesale** —
  SummerStage shows are free family-accessible park concerts and the
  maintainer wants all of them, not just family-billed ones. Keep the shared
  `ADULT_BLOCKLIST` title/body safety net; exclude the non-event categories
  (`Volunteer: It's My Park`, `Partnerships for Parks`, `Grants and More`)
  unless a row is clearly an attendable family event. Fixture capture must be
  off-proxy (Cloudflare 403s plain httpx).
- **Why:** high potential yield — this is the nonprofit behind **SummerStage**
  (free concerts across many NYC parks), the **Puppet Mobile** (free puppet
  shows touring parks, explicitly kids' programming), and the **Charlie
  Parker Jazz Festival**, plus other citywide free programs (sports, arts
  education). Unlike a single venue, this is a citywide multi-park
  aggregator — closer in shape to the permit source but editorially curated
  (real descriptions/URLs, not permit noise).
- **Platform (confirmed 2026-07-13):** WordPress + The Events Calendar
  (Tribe), REST API open — see the Status bullet for the endpoint.
- **Filtering plan (decided — maintainer call 2026-07-13):** category
  allowlist: **`PuppetMobile` + `SummerStage`, both wholesale** (the earlier
  "SummerStage only if family-billed" idea is superseded — the maintainer
  wants every SummerStage show). Shared `ADULT_BLOCKLIST` stays on as a
  safety net; the admin-ish categories (volunteer/grants/partnerships) stay
  out.
- **Borough/venue — citywide, per-event:** each event happens at a different
  park across multiple boroughs (SummerStage alone runs in Central Park,
  Prospect Park, Coney Island, St. Mary's Park, etc.) — this needs a
  **per-event venue/borough field from the source**, not a hardcoded
  constant, similar to the NYPL borough requirement. If venue names match
  existing parks, `park_neighborhoods.json` may already cover neighborhood
  coding for many rows — worth checking coverage during the probe before
  assuming gaps.
- **Open question:** does the feed expose per-event structured data (dates,
  park, program), or is it more editorial/prose like a season announcement?
  Gauge during the probe — same caution as The Skint below.

### Gothamist

- **Status:** CANDIDATE — proposed 2026-07-06, unprobed. **Likely not a kids
  event source** — flagged for evaluation, not assumed buildable.
- **What it is:** NYC news/culture site (WNYC-owned). Not a dedicated events
  calendar — occasional "things to do with kids this weekend" roundup posts,
  similar in spirit to The Skint but even less event-structured (it's a news
  site, not an events blog).
- **URLs to probe:** `https://gothamist.com/feed` or `/arts-entertainment/feed`
  (WordPress-style RSS, unconfirmed), and check for a dedicated kids/family
  tag/category feed.
- **Same two blocking questions as The Skint (settle first):**
  1. **Per-event or digest/roundup articles?** Gothamist's kids content is
     almost certainly roundup articles ("32 things to do with kids in NYC
     this weekend") listing many events in prose, not one item per event.
     Extracting structured events from that prose is free-text NLP —
     **explicitly out of scope** (PHASE-3-PLAN.md). If every kids-relevant
     post is this shape, this candidate is **not buildable** without an
     out-of-scope NLP step and should be rejected outright.
  2. **Kid yield.** Even if some items are per-event, Gothamist is a general
     news site — expect most content to be unrelated to kids/family events
     entirely (politics, food, transit). A strict allowlist would be
     mandatory.
  - **Recommendation:** probe briefly to confirm/reject the digest-format
    problem before investing more time — this is the weakest candidate of
    the group and may be a fast REJECTED.
- **Filtering plan if built (only if per-event structure exists):** mandatory
  kid-relevance allowlist + the shared `ADULT_BLOCKLIST`/
  `ADULT_TITLE_BLOCKLIST` from `_filters.py`, same posture as The Skint.
- **Missing-detection:** opt out (`window_days=None`) if built — editorial
  rotation, not a full-window feed.

### The Skint (theskint.com) — citywide editorial RSS

- **Status:** CANDIDATE — probed 2026-07-06 (plain `httpx`, no anti-bot; `curl_cffi`
  actually got connection-reset from this sandbox — the reverse of the usual
  pattern, so try plain `httpx` first for this host). Both blocking questions
  from the original entry are now answered. **Verdict: technically buildable
  without AI/NLP, but yield is low — a real judgment call, not an easy win.**
- **What it is:** a long-running NYC "free & cheap things to do" editorial blog
  (WordPress). Citywide aggregator — **not** a venue and **not** a kids feed.
- **Endpoints confirmed:** `https://theskint.com/feed/` (RSS, 10 most recent
  items) and `https://theskint.com/wp-json/wp/v2/posts?per_page=20` (REST API,
  same recent window — **the API caps at 19 total posts**, it does not expose
  deep history; older post URLs found via `sitemap.xml` → `sitemap-index-5.xml`
  → `sitemap-3.xml`/`sitemap-4.xml` now 404 — looks like old posts are pruned,
  not just unlisted, so don't plan on backfill).
- **Q1 answered — item granularity is mixed, and only half the mix matters:**
  Of 19 recent posts, **8 are digest/roundup posts** (title pattern
  `"DAY-DAY, M/D-M/D: ..."` or `"...SKINT WEEKEND"`) and **11 are standalone
  single-event posts**, mostly tagged "(SPONSORED)" — paid ad placements for
  comedy shows/movie promos, almost all adult content, with unstructured
  prose dates ("On July 8...", "July 21 & 22"). **Recommendation: skip
  standalone posts entirely** — low volume, low kid-relevance, no structured
  date field worth the parsing effort. All real value is in the 8 digest posts.
- **Digest posts ARE templated, not free prose** — confirmed by parsing all 8
  live: each is `<u>day-name</u>` section headers containing one `<p>` per
  event in the form `<day/time-phrase>: <b>Title</b>: description. <a href=...>`.
  A regex (`^(prefix text): <b>(title)</b>:?\s*(description)`, prefix chars
  must include `:` since times like "8:30pm" contain one) matched **239 of 472
  `<p>` blocks** across the 8 posts (~30 events/post). The remainder is mostly
  boilerplate (day headers themselves, empty `<p>`, "sponsored"/"note:"/
  "support us" blocks) plus one gotcha: **~40% of matched events have
  multi-paragraph descriptions** — the continuation `<p>`s that follow don't
  match the event-start pattern and must be folded into the previous event's
  description (a small state machine, not a single regex pass).
- **A separate "ongoing" section deliberately excluded:** each digest ends with
  a "roundup of 70+/80+ ongoing events" prose blurb (standing weekly programs —
  free pools, Shakespeare in the Park, etc.). No per-item dates exist here;
  treat as unparseable and skip, same reasoning as not modeling a "things you
  can do anytime" blurb as dated Events.
- **Time-phrase → date:** the digest title's own date range (e.g. "7/3-6")
  anchors each named weekday header to a real calendar date (combine with the
  post's `pubDate` year). Recurring/vague phrasing inside individual events
  ("monthly", "while supplies last", "thru the season") is real and common —
  **no attempt to model true recurrence** (unlike Domino Park's `variant`
  field); anchor to the day-header's date and leave the phrase in the
  description, same "unparseable time → midnight" leniency as Brooklyn Army
  Terminal.
- **Venue extraction — better than expected:** ~50% of matched events end
  their description with a `Venue Name (neighborhood)` clause before the final
  period — e.g. "halyards (gowanus)", "caveat (les)", "the flea theater
  (tribeca)", "pioneer works (red hook)". The neighborhood token is usually a
  recognizable NYC-abbreviation (les/uws/dumbo/etc.) that could map onto
  existing NTA labels via a small alias table (reuse `_neighborhoods.py`
  machinery — **no geocoding needed** for these rows). A tighter extraction
  regex than my quick probe is needed (naive matching grabbed garbage like
  "with directors charlie ahearn" as a venue on a few rows) — worth getting
  right since it's half the events. The other ~50% get `venue=None`,
  `low_confidence=True`.
- **Kid yield — the real gating number:** ran the actual shared filter
  (`_filters.py` `ADULT_BLOCKLIST`/`ADULT_TITLE_BLOCKLIST`/`MEMBERS_ONLY`) plus
  a draft kid-keyword allowlist against all 239 parsed events: **14 kept
  (5.9%)**, e.g. "Free Outdoor Movies" (recurring, appears across several day
  headers — likely 1 real series double-counted several times, not 6 distinct
  events), Jersey City Fourth of July Festival, Punk Island, Free Bike Helmets,
  Museum Mile Festival. That's roughly **3–5 truly distinct kid-relevant
  events per week** after accounting for the recurring-series double-count —
  well above Coney Island USA's ~2% rejection floor, but far below the density
  of the built park/museum sources (Prospect Park ~300/60-day window,
  Governors Island ~85/100). A real allowlist would likely do somewhat better
  than my quick draft, but this is a low-density source, not a high-value one.
- **`external_id`:** no per-event id upstream — `compute_id` fallback to
  `title|date`, same pattern as Brooklyn Army Terminal.
- **Missing-detection:** opt **out** (`window_days=None`, like `mommy_poppins`)
  — an editorial feed rotates posts incrementally, so an unmodified item
  leaving a recent window isn't a cancellation.
- **Open decision:** buildable without AI/NLP, but it's the messiest parser in
  the codebase (day-header segmentation + paragraph continuation-folding + a
  ~50%-hit venue regex) for a modest ~3–5 events/week yield. Worth it mainly if
  citywide breadth (vs. single-venue depth) is the priority. Not yet built —
  maintainer call on whether the yield justifies the parser complexity.

---

## Deferred to Phase 3+ (headless browser required)

The Phase 2 editorial-source backlog is otherwise built or rejected. Brooklyn
Cyclones — the one remaining CONFIRMED venue — is deferred to Phase 3 because
the themed-night data that makes it worth shipping needs a headless browser
(a new dependency, drawn as the Phase 2 boundary). See "The themed-night
problem" below.

### Brooklyn Cyclones

- **Status:** DEFERRED to Phase 3+ — the game schedule is CONFIRMED and
  buildable today, but the themed nights that give it family-planning value
  need a headless browser (see "The themed-night problem").
- **Source:** MLB Stats API — `https://statsapi.mlb.com/api/v1/schedule`
- **Format:** public JSON API, no key, no anti-bot
- **Team:** `teamId=453`, venue "Maimonides Park" (Coney Island)
- **Fetch home schedule:**
  ```bash
  curl -s "https://statsapi.mlb.com/api/v1/schedule?sportId=13&teamId=453&startDate=2026-04-01&endDate=2026-09-30&gameType=R"
  ```
- **Data shape:** `dates[].games[]` — each game has `gamePk`, `officialDate`,
  `teams.home`/`away` (name + id), `venue.name`, `gameDate` (UTC ISO).
  Also available via `&hydrate=tickets`: per-game `ticketLinks.home` URL
  (e.g. `https://mlb.tickets.com/?orgid=58029&agency=MILB_MPV&eventId=XXXX`).
- **Build notes:** ingest home games only (`teams.home.team.id == 453`).
  `external_id = str(gamePk)` — stable per-game. Synthesize title
  ("Brooklyn Cyclones vs {away}"), set `low_confidence=False`.
  Tag `sports`/`family`. No description/age fields from this source.
- **ToS:** unofficial public API; widely used. Cache aggressively.

#### The themed-night problem

The main family-planning value of Cyclones games is themed nights and
giveaways (Star Trek Night, Hot Dog Run, Bark in the Park, bobbleheads,
fireworks, etc.). **None of this data is available through the MLB Stats
API** — `hydrate=promotions` returns zero results for all Cyclones home
games. The promotions live in Contentful CMS (space `iiozhi00a8lc`) and
are only loaded at JS runtime by the browser; there is no public access
token and no server-rendered data on the promotions page.

**Future phase option — two-source approach:**

Combine the Stats API (stable game IDs, dates, opponents) with a
promotions scrape that uses a headless browser to render
`https://www.milb.com/brooklyn/tickets/promotions`, extract promo names,
and join them back onto games by date. Rough shape:

1. `hydrate=tickets` gives you the `eventId` for each game on
   `mlb.tickets.com`. That page may also render the promo name — not
   confirmed yet (sandbox blocked the fetch).
2. The promotions page at `/brooklyn/tickets/promotions` lists themed
   nights linked by date. A Playwright render + parse would capture them.
3. Merge promos onto game rows at ingest time; write as `description`.

This is out of scope for Phase 2 (headless browser = new dependency).
Revisit in Phase 3+ if a simpler path turns up.

**Research needed before building:**

- [ ] From your laptop, fetch a `mlb.tickets.com` event page for a known
  Cyclones game and check whether the event title includes the promo name
  (e.g. "Star Trek Night — Brooklyn Cyclones vs Hudson Valley Renegades").
  Use: `curl -sL "https://mlb.tickets.com/?orgid=58029&agency=MILB_MPV&eventId=4046"`
  and look at `<title>` and any JSON-LD. If yes, this is the simplest path —
  no headless browser needed, just a second fetch per game.
- [ ] Check whether `https://www.milb.com/brooklyn/tickets/promotions`
  has any server-rendered data (e.g. `__NEXT_DATA__` or JSON-LD) when
  fetched with `curl_cffi` — sandbox confirmed it renders zero embedded
  data with a plain curl, but Chrome impersonation might get SSR'd content.
- [ ] Search for a Contentful public delivery token in the MiLB page JS
  bundles (the space ID is `iiozhi00a8lc`). If found, the Contentful
  Delivery API (`cdn.contentful.com/spaces/{space}/entries?content_type=promotion&...`)
  is the cleanest structured path.

---

## Built — original build spec (reference)

### Brooklyn Army Terminal

- **Status:** BUILT — shipped as source `brooklyn_army_terminal`
  (`src/nyc_events/sources/brooklyn_army_terminal.py`). See the as-built
  block under "Built — research vs. as-built" below.
- **Source:** Drupal (NYCEDC site) — `https://brooklynarmyterminal.com/events`
- **Auth:** Requires `curl_cffi` (`impersonate="chrome"`) — Cloudflare blocks
  plain httpx/curl.
- **Format:** Single-page HTML, all events server-rendered. No pagination.
  27 events total (as of 2026-06-06), covering Jun–Oct 2026.
- **HTML structure:**
  ```html
  <div class="events-full-width__grid-card" data-month="06">
    <div class="card card--event">
      <div class="card__date">
        <div class="date__left"><div class="day">07</div></div>
        <div class="date__right">
          <div class="month">June</div>
          <div class="year">2026</div>
          <div class="time">1:00-7:00pm</div>
        </div>
      </div>
      <div class="card__title">Summer at the Terminal: Ferry Food Fest 2026</div>
      <div class="card__subtitle">description...</div>
      <!-- optional: <a href="external-url"> wrapping the card -->
    </div>
  </div>
  ```
- **Filtering — critical:** 13 of 27 events are adult EDM nightclub concerts
  ("Live Music Concert with Teksupport / Project 91 / EMW Presents"),
  ticketed via dice.fm or posh.vip. These are 21+ paid events, not kid-
  relevant. **Exclude any event whose title starts with "Live Music Concert".**
  Kid-relevant events (~14): Summer at the Terminal markets, food fests,
  cultural festivals, Rooftop Films screenings, Community and Family Day,
  Hispanic Heritage Festival, Día de Los Muertos.
- **Build notes:** no stable `external_id` in the HTML — derive from
  `title|date` via `compute_id` fallback. External URL from `<a href>` when
  present; otherwise leave `url=None`. Venue = "Brooklyn Army Terminal",
  borough = BROOKLYN. All community events are free; concerts are PAID —
  set price based on whether the external link is to dice.fm/posh.vip.
  Fetch: `curl_cffi` GET of the single events page, parse with selectolax.
  Full-window single-page fetch → set `window_days` for missing-detection.

---

## Ready to build — confirmed structured feed

### Industry City — ✅ BUILT (live) — Tribe REST API

- **As built (2026-06-20):** slug `industry_city`, `IndustryCitySource`,
  copy-adapted from `prospect_park.py` / `ny_transit_museum.py`. Registered in
  `ENABLED_SOURCES` among the fast Tribe REST sources (after Prospect Park,
  before the permit source). Fixture `tests/fixtures/industry_city_sample.json`
  drives `tests/test_industry_city_parse.py` (24 tests). `window_days = 60`,
  opted into missing-detection.
  - **Real built numbers:** a live 60-day fetch (2026-06-20) returned **29 rows
    → 16 dropped, 13 kept** (T-Shirt Yarn Workshop, BCR Mending Circle,
    Puppetworks KIDS + Community Reception, Zine Club ×4, and the 5 outdoor
    World Cup watch parties). Note: the larger "~195 events / total_pages=13"
    probe used a ~2-year window; the production 60-day window is much smaller
    (~29 rows). The 15-row fixture (`per_page=15` page 1) yields 9 kept under
    the same filter.
  - **Confirmed vs. research:** `external_id = str(id)` held — the gourmet tour
    appears twice in the fixture with distinct ids (10051523 / 10051524) and
    dated URL slugs, so the Tribe-per-occurrence precedent is confirmed; no
    `:start.isoformat()` suffix. `cost` and `venue` were empty across every
    surveyed row, as predicted → price UNKNOWN for all, venue/borough hardcoded
    Industry City / Brooklyn, no lat/lng/age.
  - **Filter as built:** keyword allowlist on title+description+excerpt (kids,
    family, workshop, craft, puppet, market, etc.); `Nightlife` category is a
    hard-exclude; a hard-exclude blocklist (21+, 18+, burlesque, drag, late
    night) wins over the allowlist. (Alcohol-tasting terms —
    cocktail/whiskey/sake/brewery/distillery/wine-or-beer tasting/happy hour —
    were later removed per the filter review, so the "gourmet food and drinks"
    tour and the sake class are now kept.) Only an explicit **"no children"** is
    treated as an adult-only
    signal. The outdoor World Cup watch parties say "NO STROLLERS or children
    under the age of 3"; the bare word "children" matches the allowlist, so
    they are **kept** as kid-friendly outdoor events. (An earlier build also
    blocklisted "no strollers" / "children under the age" to drop them, but
    those phrasings wrongly catch legit kid events that merely ban strollers
    or price by age, so they were removed.)

## The "non-impersonating probe" lesson (resolved)

> Three sources — **Industry City**, **Governors Island**, and **Domino
> Park** — were each rejected with a "headless CMS, no public feed" verdict
> that turned out to be a bot-block artifact: the original probe didn't
> impersonate a browser, ate a 403, and never reached the real feed. All
> three were re-probed with `curl_cffi` (`impersonate="chrome"`) and BUILT
> (see the Built section). **Lesson: always probe candidate sources with
> `curl_cffi` impersonation before concluding "no feed."** No backlog
> candidates currently carry an unverified rejection.

### Domino Park — ✅ BUILT (live)

- **Status:** BUILT — shipped as source `domino_park`
  (`src/nyc_events/sources/domino_park.py`). The "Sanity headless, no public
  feed" verdict was a non-impersonating-probe artifact.
- **Source:** `https://www.dominopark.com/events` (Next.js App Router + Sanity).
- **Platform:** Sanity CMS. The `production` dataset on project `4shd8slw`
  allows anonymous reads, so we query the public GROQ API directly — no HTML
  scraping, no headless browser.
- **Endpoint:** `https://4shd8slw.apicdn.sanity.io/v2021-10-21/data/query/production`
  with GROQ `*[_type=="event"]{...}`. `curl_cffi` (`impersonate="chrome"`); the
  apex domain bot-blocks plain fetchers.
- **As-built notes:**
  - **`variant` is the authoritative recurrence switch, NOT `frequency`.**
    `reoccurring` docs are a single series → expanded via `frequency`
    (weekly/monthly/daily) + `interval` (every-N) bounded by
    `startDate`..`endDate`, one row per occurrence
    (`external_id=f"{_id}:{date}"`). `single-day`/`multi-day` docs are one
    event each; they OFTEN carry leftover `frequency`/`interval`/`endDate` from
    a template (e.g. "Longevity Stick" and "Horticulture Tours" each exist as
    several single-day docs, some with `endDate` < `startDate`) — that data is
    VESTIGIAL and must be ignored, or rows both double-count and emit garbage
    dates. The two representations don't overlap upstream.
  - `startHour`/`endHour` are free-text ("6 pm", "10:00 AM", "7:30 pm ",
    "8:00am") parsed leniently; unparseable → midnight. Times are local
    wall-clock → America/New_York.
  - Rich: `description` (plain text), `latitude`/`longitude` (~98% of docs),
    `tags` (category labels mapped to our tags), `slug` (→ `/events/{slug}`).
    No price field → UNKNOWN. Venue/borough hardcoded "Domino Park" / Brooklyn
    (Williamsburg waterfront); per-event `location` kept in `raw_payload`.
  - Inclusive + light blocklist (curated family-park feed; tags dominated by
    "Family & Education"). Only strong adult signals dropped (21+, burlesque,
    "drag show"/"drag brunch"); bare "drag" is NOT blocked (catches family
    throwback/skate nights). (Alcohol-tasting terms — wine/beer tasting, happy
    hour — were later removed per the filter review.)
  - Opted INTO missing-detection (`window_days=60`): the GROQ query returns the
    full event collection each run and occurrence ids are deterministic, so a
    fetch is a true full-window re-fetch.
  - As built (2026-06-20): 125 docs → 104 events over a 60-day window.

### Governors Island — ✅ BUILT (live)

- **Status:** BUILT — shipped as source `governors_island`
  (`src/nyc_events/sources/governors_island.py`). The prior "custom CMS, no
  API surface" verdict was a non-impersonating-probe artifact (same failure
  mode that wrong-flagged Industry City). There IS a clean JSON feed.
- **Source:** `https://www.govisland.com/things-to-do.json`
- **Platform:** custom Craft CMS / Solspace-Calendar controller (NOT WordPress
  + Tribe — `/wp-json/...` returns the bot-block HTML page). The page's Vue
  `eventsApp` bundle calls `GET /things-to-do.json`; that's the endpoint.
- **Fetch:** `curl_cffi` (`impersonate="chrome"`) — plain fetchers get a
  bot-block HTML page even on `/wp-json` paths.
- **As-built notes (differ from original research):**
  - Returns `{"data": [...], "meta": {...}}`; each row is one event entry
    carrying its NEXT upcoming occurrence (`meta.criteria` =
    `loadOccurrences:next, rangeStart:now, orderBy:id asc`).
  - **Dates are "floating" local wall-time mislabeled as UTC.** `startDate`
    reads e.g. `2026-07-25T12:00:00.000000Z` but the event is noon *local*
    (calendar `icsTimezone` = "floating"). We strip the bogus `Z`, parse naive,
    and attach `America/New_York`. Treating it as UTC would shift every event.
  - `id` is per-event and unique (100 distinct ids in a 100-row fetch);
    recurring events surface only their next occurrence, so
    `external_id = str(id)` — no `:start.isoformat()` suffix.
  - **The feed hard-caps at 100 rows ordered `id asc`** — no pagination param
    works (`?limit`/`?per_page`/`?page`/`?offset` all ignored). Because the cap
    is id-ascending, newer (higher-id) listings fall off the end if total > 100.
    So a fetch is NOT a guaranteed full window re-fetch → **opted OUT of
    missing-detection** (`window_days=None`, same caution as mommy_poppins).
  - Filtering is **inclusive + blocklist**: GI skews family, so include by
    default and drop only a focused blocklist — adult-only signals (21+,
    burlesque), title-level adult/non-event terms (gala, beach club,
    after-party, open bar, bike rentals, the QC NY spa, the digital guide), and
    competitive road races (NYCRUNS 5K/10K/marathon). (Alcohol-tasting terms —
    cocktail, wine/beer tasting, happy hour — were later removed per the filter
    review; "open bar" stays.) An allowlist
    was rejected: it would drop keyword-less kid gold ("Slide Hill", "Hammock
    Grove Play Area").
  - `cost` absent upstream → price UNKNOWN. No lat/lng, no age range.
    venue/borough hardcoded "Governors Island" / Manhattan (the island is part
    of the Borough of Manhattan); per-event `locations[].locationName` kept
    only in `raw_payload`.
  - As built (2026-06-20): a live fetch returned 100 rows → 15 dropped, 85
    kept across four calendars (Events, Ongoing Programs, Recreation, Public
    Art).

---

## Built — research vs. as-built

Shipped sources, kept here for the "research said X, reality was Y" record.
Source code is authoritative; these notes capture the surprises.

### Green-Wood Cemetery — ✅ BUILT (live)

- **Status:** BUILT — shipped as source `greenwood_cemetery`
  (`src/nyc_events/sources/greenwood_cemetery.py`).
- **Source:** WordPress + The Events Calendar REST API
- **Endpoint:** `https://www.green-wood.com/wp-json/tribe/events/v1/events`
- **Pagination:** `?per_page=50&page=N`, follow `next_rest_url` until absent.
- **Fetch:** `curl_cffi` (`impersonate="chrome"`) — plain httpx would 403.
- **As-built notes (differ from original research):**
  - `cost` is **always empty** on both the list and single-event endpoints
    (`cost_details.values` is `[]`), so price is `UNKNOWN` for all events.
    Pricing lives in a ticketing widget the API doesn't expose. The
    cost→Price mapping is kept for when/if upstream populates it.
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
  - Kid-relevance: keyword allowlist (family, nature, music, storytelling,
    holidays, film, tour, etc.) + soft blocklist (gala, donor, adults only;
    `cocktail` removed per the filter review). `members only` / `members-only`
    in the **title** is a
    hard exclude that overrides any allowlist hit.
  - ~104 kid-relevant events in a 60-day window (verified live).

### Prospect Park Alliance — ✅ BUILT (live)

- **Status:** BUILT — shipped as source `prospect_park`
  (`src/nyc_events/sources/prospect_park.py`).
- **Source:** WordPress + The Events Calendar REST API
- **Endpoint:** `https://www.prospectpark.org/wp-json/tribe/events/v1/events`
- **Pagination:** `?per_page=50&page=N`, follow `next_rest_url` until absent.
- **Fetch:** `curl_cffi` (`impersonate="chrome"`) — Cloudflare blocks plain
  fetchers.
- **As-built notes (differ from original research):**
  - **`external_id = str(id)`, NOT slug-from-url.** The original research
    claimed recurring events share a Tribe `id`; live verification
    (2026-06, 456 events / 60-day window) showed the Tribe `id` IS
    per-occurrence — 456 distinct ids and 456 distinct dated URL slugs.
    Recurring events get a new id per occurrence (e.g. Wednesday
    Greenmarket: 10000742, 10000743, …). No `:start.isoformat()` suffix
    needed.
  - Category filter as researched: "Kids", "Audubon Center", "Carousel",
    "Lefferts Historic House", "Nature Programs", "Film",
    "Performing Arts", "Education" — all names verified live (Kids=124,
    Audubon=176, Nature=95, Lefferts=107, Carousel=17, Education=18,
    Performing Arts=8, Film=4 in a 60-day window; counts are
    per-occurrence, much higher than the original per-series counts).
  - Defensive title hard-exclude ("21+", "adults only", "members only")
    overrides any included category. No live events currently trigger it —
    the included categories are clean (checked for adult-content leakage).
  - `cost` is populated (unlike Green-Wood): "Free" variants → FREE,
    `$` → PAID, "Prices Vary"/empty → UNKNOWN.
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
  - Venue always empty upstream as researched — hardcoded
    venue = "Prospect Park", borough = BROOKLYN. No lat/lng, no age range.
  - ~307 kid-relevant events of 456 total in a 60-day window (verified live).

### New York Transit Museum — ✅ BUILT (live)

- **Status:** BUILT — shipped as source `ny_transit_museum`
  (`src/nyc_events/sources/ny_transit_museum.py`).
- **Source:** WordPress + The Events Calendar REST API (same Tribe plugin
  as Green-Wood and Prospect Park — third instance, copy-adapt of
  `prospect_park.py`)
- **Endpoint:** `https://www.nytransitmuseum.org/wp-json/tribe/events/v1/events`
- **Pagination:** `?per_page=50&page=N` + `start_date`/`end_date` params,
  follow `next_rest_url`. Small calendar: 26 events / 60-day window —
  single page in practice; pagination loop kept.
- **Fetch:** `curl_cffi` (`impersonate="chrome"`) — plain default-UA
  fetchers get 403.
- **As-built notes (verified live 2026-06-10 during the build):**
  - **`external_id = str(id)`** — re-verified against the captured window:
    26 events → 26 distinct ids; recurring programs (Transit Tots ×7,
    Old City Hall tour ×3, anniversary shuttle rides ×2) each get a
    distinct id and dated URL slug per occurrence. No date suffix.
  - **Venue is a real per-event object** as researched. Live values:
    "New York Transit Museum, Brooklyn" (13 — city="Brooklyn",
    geo_lat/geo_lng populated, so lat/lng ARE set for museum events),
    "Off-Site" (10 — no city, no geo → borough/lat/lng None, no
    guessing), "Virtual" (3 — excluded by category anyway). Borough is
    mapped from the venue `city` field via a city→Borough lookup.
  - **Category allowlist {Family Programs, Nostalgia Rides}**; hard
    exclusion {Members-Only Programs, Virtual Programs} wins over any
    allowlist overlap. "Special Event" (2) was NOT added: both live
    instances also carried "Nostalgia Rides", so it adds nothing.
  - **Known dropped kid-relevant edge cases (deliberate):**
    "Subway Simulator Sunday" ships with `categories=[]` and "Special Day"
    (sensory-friendly program for children with disabilities) is
    categorized only "Access Programs" — both fall outside the allowlist.
    Widen the allowlist later if these matter.
  - `description` is empty on the list endpoint; text lives in `excerpt`.
  - `cost` populated: "$40", "$35 – $40", "$10 – $20", "Free", and
    "Included with Museum admission" (mapped to PAID — admission is paid).
  - Use `utc_start_date` / `utc_end_date` directly — no local-tz conversion.
  - No age fields upstream (Transit Tots is toddler-aimed but unstructured).
  - 10 kid-relevant of 26 total in a 60-day window (verified live).

### Brooklyn Army Terminal — ✅ BUILT (live)

- **Status:** BUILT — shipped as source `brooklyn_army_terminal`
  (`src/nyc_events/sources/brooklyn_army_terminal.py`).
- **Source:** Single-page server-rendered HTML —
  `https://brooklynarmyterminal.com/events`. No pagination.
- **Fetch:** `curl_cffi` (`impersonate="chrome"`) — Cloudflare blocks plain
  fetchers. The `www.` host 403s; the non-www host is correct.
- **Parse:** selectolax on `.events-full-width__grid-card` cards. Date from
  `.day` / `.month` / `.year`; start time from `.time`; title from
  `.card__title`; description from `.card__subtitle`; URL from the card's
  `<a href>` when present.
- **As-built notes (verified live 2026-06-15 during the build):**
  - **Counts:** 24 cards on the captured page (matches live), **12 dropped**
    "Live Music Concert" 21+ EDM shows, **12 kept** community/family events —
    NOT the ~27-total / ~14-kept the original research estimated (the page
    shrank between 2026-06-06 research and the 2026-06-15 build). The
    filter rule (title startswith "Live Music Concert") is unchanged.
  - **`external_id = None`** as researched — there is no per-event id and
    most cards have no detail URL, so `compute_id` falls back to
    `title|venue|date`. Verified the 12 kept events produce 12 distinct ids
    (no two kept community events share a date+title on the captured page).
  - **`url`** is the card's external `<a href>` when present (Rooftop Films
    calendar, a Facebook page, artbuilt.org) and `None` otherwise. The
    dice.fm / posh.vip links only appear on the dropped concert cards.
  - **Price:** all 12 kept events are `FREE`. The dice.fm/posh.vip → `PAID`
    rule is kept defensively but never fires on a kept card after filtering.
  - **Time parsing:** `.time` is a range like "1:00-7:00pm" /
    "10:00am-2:00pm"; we parse the START only and borrow am/pm from the end
    of the range when the start omits it. Unparseable/empty time → 00:00
    (all-day). Times are NY wall-clock; we attach America/New_York so the
    Event is tz-aware (db._iso normalizes to UTC on write and rejects naive
    datetimes — the initial build stored them naive, which crashed ingest;
    fixed 2026-06-15).
  - **`window_days = 60`** — full-window single-page re-fetch every run, so
    it opts into missing-event (possible-cancellation) detection.
  - Venue = "Brooklyn Army Terminal", borough = BROOKLYN (hardcoded). No
    lat/lng, no age range, no end time (`end_dt = None`). Tags inferred from
    title keywords (always includes "family").

---

## Rejected

### Coney Island USA — ❌ REJECTED (feed works; content isn't kid-relevant)

- **Status:** REJECTED 2026-06-10 after full content review. The endpoint is
  technically fine — this is a content rejection, not a technical one.
- **Source:** Squarespace — `https://www.coneyisland.com/event?format=json`
- **What the probe found (live capture, 20 upcoming + 30 past events):**
  - **Zero kid-relevant events upcoming** (June–Sept window): the calendar
    is Burlesque at the Beach, Prideshow at the Sideshow, adult variety,
    drag film nights, sideshow classes, and lectures — wholesale.
  - Past 30 events: same profile. Exactly one kids' item ("Congress of
    Curious Peoples: Curious Kids Workshop") and one CANCELED youth show.
    ~2% historical kid yield.
  - **The Mermaid Parade is NOT in this feed** — absent from both arrays
    nine days before the 2026 parade. The flagship family event is
    published elsewhere on the site, so "build it and the parade will
    flow in" does not hold.
- **Corrections to the original research, if ever revisited:** `location`
  is an object (mapLat/mapLng/addressTitle), not a string; venue varies
  per-event (Coney Island Museum / Coney Island USA / Freak Bar);
  Squarespace `id` is per-occurrence (recurring titles get distinct ids);
  plain curl with a browser UA works — no curl_cffi strictly needed.
- **Revisit if:** they start publishing family programming (Curious Kids,
  all-ages matinees) regularly, or the Mermaid Parade/film festival move
  into the event collection. A strict title/category allowlist version is
  ~20 minutes of work on top of the Squarespace fast-path if that happens.

### Time Out NY Kids — ❌ REJECTED (re-probed 2026-07-06; reason updated)

- **Status:** REJECTED — re-probed 2026-07-06 per the "non-impersonating
  probe" lesson. The rejection **stands**, but the original reason is stale;
  don't trust the old "needs a headless browser" framing.
- **What changed since the original probe:** the site is **server-rendered
  now** — plain `httpx` with a browser UA gets full content, no anti-bot, no
  JS rendering needed. The original "JS-rendered, no structured data" verdict
  no longer describes the site.
- **What the re-probe found:**
  - **The kids vertical (`/new-york-kids`) has no dated events at all.** Its
    "things to do" hub is evergreen listicles only ("101 things to do with
    kids", "25 best playgrounds") — nothing with a date to ingest. The old
    kids events calendar URL 404s.
  - **The main NYC monthly events calendar**
    (`/newyork/events-calendar/<month>-events-calendar`) is real: ~58
    numbered, server-rendered items/month, each tile linking to a detail
    page. Detail pages carry a structured info box (Address / Price /
    Opening hours / Event website) and a `Review` JSON-LD whose
    `itemReviewed` is typed `TheaterEvent` **with an `offers.price` field
    but NO `startDate`** — one schema field short of buildable.
  - **Event dates exist only mid-sentence in editorial prose** ("On July 11,
    New York City Lab School…", "Thursday nights throughout July and
    August, plus a special family movie night in September"). Unlike The
    Skint's deterministic `fri 7pm:` prefixes, there is no positional or
    templated date token — extracting `start_dt` here is free-text NLP,
    explicitly out of scope (PHASE-3-PLAN.md).
  - **Kid yield of the general calendar is low anyway:** a quick pass of the
    58 July items through `_filters.py` + a draft kid allowlist kept 3
    (~5%) — and the calendar's kid-relevant series (Movies with a View, NYC
    Math Festival) are venues/programs we can cover directly (Brooklyn
    Bridge Park is already a CANDIDATE).
- **Revisit if:** Time Out adds `startDate` to the JSON-LD (the
  `TheaterEvent` typing suggests the CMS knows it's an event — they're one
  field away), or a dated "When" row appears in the detail-page info box.
  Check the JSON-LD first on any future probe; it's the cheapest tell.
- Stub kept at `src/nyc_events/sources/timeout_nykids.py` as a tombstone
  (raises `NotImplementedError`); don't implement or delete it.
