# Future source backlog (candidates — verify before building)

Research notes for NYC venues proposed for future Phase 2 integration.
Before any CANDIDATE becomes a real source, run the verification probe
**from outside the sandbox** (your laptop or the NAS) and update its
status to `CONFIRMED` or `REJECTED`. Only then run the `source-adder` recipe.

## Status legend

- `CANDIDATE` — plausible source found, format guessed or partially confirmed in-sandbox.
- `CONFIRMED` — probed from outside the sandbox; format + endpoint verified.
- `REJECTED` — probed, no usable structured source.

## Cross-cutting notes

**Anti-bot 403s.** Consumer-facing sites (Industry City, Domino, Green-Wood,
Governors Island, Coney Island, Brooklyn Army Terminal) return 403 to plain
fetchers — expect to need `curl_cffi` (`impersonate="chrome"`) for all of
them. The MLB Stats API is the sole exception (it's a JSON API, not a page).

**Sandbox egress.** Cloud/web Claude sessions can't reach most of these
domains — capture fixtures on your laptop or NAS, then bring them in.

## How to verify (run OUTSIDE the sandbox)

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

## Priority 1 — CONFIRMED, ready to build

### 1. Brooklyn Cyclones

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

### 2. Green-Wood Cemetery — ✅ BUILT (live)

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

### 3. Coney Island USA

- **Status:** CONFIRMED
- **Source:** Squarespace — `https://www.coneyisland.com/event?format=json`
- **Format:** Squarespace JSON, `upcoming` array (not `items`)
- **Data shape:** each item has `title`, `startDate` (epoch ms), `location`
  (string "1208 Surf Avenue, Brooklyn, NY, 11224"), `fullUrl` (relative slug),
  `body` / `excerpt` for description.
- **Fetch:**
  ```bash
  curl -s "https://www.coneyisland.com/event?format=json"
  ```
- **Build notes:** `external_id` = item `id` or slug. Convert epoch-ms to
  datetime. Prefix `fullUrl` with `https://www.coneyisland.com` for absolute URL.
  Venue = "Coney Island USA", borough = Brooklyn.
- **Note:** This is **Coney Island USA** (sideshow, Mermaid Parade, film fest).
  Not Luna Park (park hours only, not events).

### 4. Prospect Park Alliance — ✅ BUILT (live)

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

---

### 5. Brooklyn Army Terminal

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

---

### 6. New York Transit Museum

- **Status:** CONFIRMED (probed 2026-06-10, from a cloud Claude session —
  this domain is reachable from the sandbox, unlike most on this list)
- **Source:** WordPress + The Events Calendar REST API (same Tribe plugin
  as Green-Wood and Prospect Park — third confirmed instance)
- **Endpoint:** `https://www.nytransitmuseum.org/wp-json/tribe/events/v1/events`
- **Auth:** plain fetchers 403 (default-UA urllib blocked); curl with a
  Chrome User-Agent succeeds. Use `curl_cffi` (`impersonate="chrome"`) per
  project precedent.
- **Pagination:** `?per_page=50&page=N` + `start_date`/`end_date` params,
  follow `next_rest_url`. Small calendar: 26 events / 60-day window —
  single page in practice.
- **Data shape:** standard Tribe record (`title`, `utc_start_date`,
  `cost`, `description` HTML, `categories`), with two differences from
  Prospect Park:
  - `venue` is a real per-event object, NOT an empty list — e.g.
    "New York Transit Museum, Brooklyn" vs "Off-Site" (subway tours meet
    in Manhattan, e.g. Old City Hall station). Don't hardcode venue;
    map it per-row, borough Brooklyn for the museum itself.
  - `cost` is populated ("$40", "$10 – $20", "$50").
- **IDs:** per-occurrence confirmed live (two occurrences of the same tour
  → distinct ids 93098 / 93102). `external_id = str(id)`, no date suffix.
- **Filtering:** category allowlist. Live 60-day counts: Family Programs=8
  (Transit Tots — toddler program, Movers and Makers family workshop),
  Nostalgia Rides=2 (vintage subway rides, very kid-friendly), Special
  Event=2. Exclude "Members-Only Programs" (3) and "Virtual Programs" (3);
  the adult Tours/Lectures fall out of the allowlist naturally.
- **Volume:** modest (~10-12 kid-relevant / 60 days) but high-quality and
  uniquely on-theme — transit-obsessed kids are a core audience. Cheap to
  build: copy-adapt `prospect_park.py`, swap the category list, add the
  per-row venue mapping.

---

## Priority 3 — Low confidence, deprioritize

### 5. Industry City

- **Status:** CANDIDATE (low — custom headless CMS)
- **Source:** `https://industrycity.com/events/`
- **Finding (in-sandbox probe):** custom-built site by Streetsense design firm.
  Not WordPress, not Squarespace. JS-rendered event list with a "Load more"
  button. No wp-json, no iCal, no structured feed detected.
- **Verify:** run the generic probe; check if there's a hidden XHR endpoint
  the JS calls (inspect Network tab in browser devtools).
- **Outlook:** likely requires scraping rendered HTML or reverse-engineering
  an internal API. Fragile. Deprioritize unless the XHR API turns up.

### 6. Domino Park

- **Status:** CANDIDATE (low — Sanity headless CMS)
- **Source:** `https://www.dominopark.com/events`
- **Finding:** Sanity CMS (CDN: `sanity-prod-domino-park.b-cdn.net`). Events
  server-rendered but no public structured feed or iCal found. Sanity has a
  public GROQ API but only if the project allows anonymous reads — unconfirmed.
- **Verify:** check if `https://www.dominopark.com/api/events` or similar
  exists; look for Sanity project ID in page source to attempt GROQ query.
- **Outlook:** likely requires HTML scraping. Lower priority than Priority 2.

### 7. Governors Island

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
