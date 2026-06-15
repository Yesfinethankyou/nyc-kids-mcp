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

## Ready to build

### Brooklyn Cyclones

- **Status:** CONFIRMED (game schedule only) / NEEDS RESEARCH (themed nights)
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

### Brooklyn Army Terminal

- **Status:** CONFIRMED
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

## Low confidence — no structured feed found, deprioritized

### Industry City

- **Status:** CANDIDATE (low — custom headless CMS)
- **Source:** `https://industrycity.com/events/`
- **Finding (in-sandbox probe):** custom-built site by Streetsense design firm.
  Not WordPress, not Squarespace. JS-rendered event list with a "Load more"
  button. No wp-json, no iCal, no structured feed detected.
- **Verify:** run the generic probe; check if there's a hidden XHR endpoint
  the JS calls (inspect Network tab in browser devtools).
- **Outlook:** likely requires scraping rendered HTML or reverse-engineering
  an internal API. Fragile. Deprioritize unless the XHR API turns up.

### Domino Park

- **Status:** CANDIDATE (low — Sanity headless CMS)
- **Source:** `https://www.dominopark.com/events`
- **Finding:** Sanity CMS (CDN: `sanity-prod-domino-park.b-cdn.net`). Events
  server-rendered but no public structured feed or iCal found. Sanity has a
  public GROQ API but only if the project allows anonymous reads — unconfirmed.
- **Verify:** check if `https://www.dominopark.com/api/events` or similar
  exists; look for Sanity project ID in page source to attempt GROQ query.
- **Outlook:** likely requires HTML scraping.

### Governors Island

- **Status:** CANDIDATE (low — custom/unknown CMS)
- **Source:** `https://govisland.com/calendar`
- **Finding:** custom site (S3-hosted assets, built by Reflexions design firm).
  No WordPress, no Squarespace, no JSON-LD, no iCal link visible.
  Events may be server-rendered but no API surface found.
- **Verify:** inspect Network tab in browser devtools for XHR calls; check
  `govisland.com/wp-json/` (unlikely but worth a try); check doNYC mirror
  (`donyc.com/venues/governors-island`) as an aggregator fallback.
- **Outlook:** likely scraping only. Heavy public/family programming makes it
  worth one more verification pass before giving up.

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
    holidays, film, tour, etc.) + soft blocklist (gala, cocktail, donor,
    adults only). `members only` / `members-only` in the **title** is a
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

### Time Out NY Kids — ❌ REJECTED (no structured feed)

- **Status:** REJECTED. JS-rendered editorial site: no JSON-LD, no API, no
  sitemap with events. Would need a headless browser — out of scope for
  Phase 2. Stub kept at `src/nyc_events/sources/timeout_nykids.py` as a
  tombstone (raises `NotImplementedError`); don't implement or delete it.
