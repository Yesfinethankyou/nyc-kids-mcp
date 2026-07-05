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

### Brooklyn Academy of Music (BAM)

- **Status:** CANDIDATE — proposed 2026-06-27, unprobed.
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

- **Status:** CANDIDATE — proposed 2026-06-27, unprobed.
- **System:** Queens only (~65 branches). Canonical domain **queenslibrary.org**
  (NOT `queenspubliclibrary.org` — that domain currently redirects to a junk
  site; don't probe it).
- **URLs to probe:** `https://www.queenslibrary.org/calendar` and the
  kids/family filter if the calendar exposes one.
- **Platform guess (verify):** library event calendars commonly run on
  **LibCal/Springshare**, **Communico**, or **BiblioCommons** — all of which
  usually expose a JSON or iCal feed. Grep the page for `libcal`, `communico`,
  `bibliocommons`, `assets.libcal`, JSON-LD `Event`. Expect anti-bot → use
  `curl_cffi impersonate="chrome"`.
- **Filtering plan if built:** curated venue, so gate to youth/family programs
  (storytime, kids workshops) by category if available, else keyword inclusion.
- **Borough/venue:** Queens; venue = branch name (so neighborhood coding via the
  library table works); borough always Queens.

### New York Public Library (NYPL)

- **Status:** CANDIDATE — proposed 2026-06-27, unprobed.
- **System:** **Manhattan + Bronx + Staten Island** (~90 branch libraries plus
  the research libraries). Building this one source is what actually unlocks the
  Bronx and Staten Island items below.
- **URLs to probe:** `https://www.nypl.org/events/calendar` (JS-rendered shell
  on a plain fetch — needs a real probe). Check for an events JSON endpoint
  under `nypl.org` / `*.nypl.org`, JSON-LD on event detail pages, or a
  LibCal/Communico backend.
- **Platform guess (verify):** NYPL's main site is a custom React/Drupal stack;
  the events system may be separate. If the listing is JS-only with no JSON
  feed, this is a **headless-browser** candidate (Phase-3 Playwright fallback) —
  decide during the probe.
- **Filtering plan if built:** gate to kids/family programs; exclude the
  adult/research-library lectures.
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

### The Metropolitan Museum of Art (The Met)

- **Status:** CANDIDATE — proposed 2026-06-28, unprobed.
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

### The Skint (theskint.com) — citywide editorial RSS

- **Status:** CANDIDATE — proposed 2026-06-28. RSS confirmed to exist by the
  proposer; item granularity + kid-yield NOT yet verified.
- **What it is:** a long-running NYC "free & cheap things to do" editorial blog
  (WordPress). Citywide aggregator — **not** a venue and **not** a kids feed.
- **URLs to probe:** `https://theskint.com/feed/` (WordPress default RSS; also
  try `/feed/atom/`, the WP REST API `https://theskint.com/wp-json/wp/v2/posts`,
  and a kids/family category feed if one exists,
  `https://theskint.com/category/<tag>/feed/`).
- **Two things the probe MUST settle (they decide whether it's buildable at all):**
  1. **One item per event, or one digest post per day?** The Skint's signature
     format is a single daily roundup post listing many events in the body. If
     RSS items are daily digests, there is no per-event `start_dt`/`venue`/`url`
     to map onto our `Event` rows without parsing free-text prose — and
     free-text event extraction is **AI/NLP, explicitly out of scope**
     (PHASE-3-PLAN.md). Only worth building if items (or a feed/REST variant)
     are per-event with structured dates.
  2. **Kid yield.** The Skint skews adult — free booze, bar nights, music, art
     openings. Like Coney Island USA, the feed can "work" technically while being
     almost entirely non-kid-relevant. Sample 30–50 items and estimate the
     kid-relevant fraction before committing.
- **Platform guess (verify):** WordPress → RSS/Atom is reliable; the WP REST API
  (`/wp-json/wp/v2/posts`) or JSON-LD may give cleaner structured fields than
  RSS. Anti-bot is unlikely on a feed, but fall back to `curl_cffi` if a plain
  fetch 403s.
- **Filtering plan if built:** mandatory kid-relevance **allowlist** on
  title/body (family, kids, all-ages, storytime, puppet, workshop) plus the
  shared `ADULT_BLOCKLIST` / `ADULT_TITLE_BLOCKLIST` from `_filters.py`.
  Default-exclude — this is an adult-leaning general feed, the opposite of the
  curated-kids feeds (`mommy_poppins`, `bk_childrens_museum`) that carry no
  filter by design.
- **Borough/venue/neighborhood:** all per-event and **only in free text** — a
  blog RSS item has no structured venue field. Borough/neighborhood would come
  from the enrich pass *iff* a parseable venue string can be extracted; expect
  many rows to resolve to `None`. Another reason to confirm item granularity first.
- **Missing-detection:** opt **out** (`window_days=None`, like `mommy_poppins`) —
  an editorial feed rotates posts incrementally, so an unmodified item leaving a
  recent window isn't a cancellation.

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

### Time Out NY Kids — ❌ REJECTED (no structured feed)

- **Status:** REJECTED. JS-rendered editorial site: no JSON-LD, no API, no
  sitemap with events. Would need a headless browser — out of scope for
  Phase 2. Stub kept at `src/nyc_events/sources/timeout_nykids.py` as a
  tombstone (raises `NotImplementedError`); don't implement or delete it.
