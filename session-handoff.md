# Session Handoff

## What was done (most recent first)

### Session (cont'd): building the confirmed backlog candidates — CPF + NYPL shipped

Continuation of the off-proxy re-probe below. Building the confirmed
candidates one at a time on branch `claude/backlog-sources-cpf-nypl-qpl`.

- **BUILT: `city_parks_foundation`** — sixth `TribeEventsSource` subclass.
  Category allowlist `{PuppetMobile, SummerStage}`, **ALL SummerStage kept
  (maintainer call this session — no shared adult blocklist applied)**.
  Borough is per-event from `venue.venue` (which holds the borough string,
  not a park name), so `venue_name` is None and neighborhood coding won't
  resolve (acceptable — borough + URL carry location). `is_virtual`/non-borough
  venues dropped. ~49 events/60d verified live. Fixture + 22 parser tests.
- **BUILT: `nypl`** — NYPL Manhattan/Bronx/Staten Island branch calendar; the
  first HTML-listing scrape behind Incapsula (`curl_cffi` impersonate).
  Scrapes per borough via the site's `city[]` filter (borough free, no detail
  crawl), kid gate is the audience cell (server audience filter is loose),
  occurrence datetime from the time cell (URL date is the canonical date, wrong
  for recurring), `external_id = url:start_iso` (URL repeats across occurrences
  + audience-union dupes; deduped in-fetch). Age from "ages 6-12", price FREE,
  Online rows dropped, venue = branch → library neighborhood table.
  **⚠️ HIGH VOLUME — thousands of events over 60 days (largest source by far);
  `window_days` caps it if the maintainer wants fewer.** Unlocks Bronx + SI
  library coverage. Fixture (one Manhattan kids page) + 24 parser tests.
  Live-verified all three boroughs populate and ids dedup.
- **BUILT: `qpl`** — Queens Public Library, ~65 branches. Drupal/Solr calendar
  behind an F5 wall (`curl_cffi`). Parses each card's embedded
  `arrJsonData_cal` JSON (visible card text is truncated). Kid gate `prgm_age`,
  age from "Kids(0-5)" bands, `date_show_timestamp` (epoch) authoritative.
  **One Event per program's NEXT occurrence, NOT per `all_times`** (QPL lists
  a recurring program once; its 40-deep `all_times` would ~26x the catalog —
  deliberately not expanded, preserved conceptually for future). venue =
  "<branch> Library" (suffix required for the enrich library lookup to fire —
  verified codes South Hollis→Hollis etc.), price FREE, online dropped. ~659
  events/60d verified live. Fixture + 15 parser tests.
- **BUILT: `intrepid`** — Intrepid Sea/Air/Space Museum (Pier 86, Manhattan).
  Drupal card grid at `/events/calendar`, paginated by **GET `?page=N`** (the
  `/views/ajax` POST pager is broken — returns page 0 every time; documented).
  Inclusive + adult-blocklist kid gate (no Family theme; drops After
  Hours/tasting/gala). `external_id = url:start_iso`, single fixed venue →
  `SOURCE_NEIGHBORHOOD["intrepid"]="Hell's Kitchen"`, price FREE-if-titled.
  ~13 events/60d verified live. Fixture + 13 parser tests.
- Full suite **715 green**, ruff clean. `test_missing_detection` census bumped
  14→18 (all four new sources opt into missing-detection).
- **Still TODO this session:** a dedup decision on the Yodel/Macaroni Kid
  widget before building it (the one remaining candidate).
- **⚠️ Two things flagged for the maintainer:** (1) NYPL volume — thousands of
  events over 60d, easily capped via `window_days`; (2) QPL granularity — next-
  occurrence-only per the reasoning above. Both are deliberate defaults, open
  to change.

### Session: off-proxy re-probe of the anti-bot backlog candidates — 4 CONFIRMED, 2 deprioritized, 1 scoped

Ran from the laptop (no MITM proxy) so `curl_cffi impersonate="chrome"` works
again. Re-probed every candidate the previous session recorded as
sandbox-blocked; **all the walls fell**. No code written — this was probe +
plan; findings recorded in each SOURCES-BACKLOG.md entry:

- **City Parks Foundation → CONFIRMED**, standard WordPress/Tribe with the
  REST API open; 82 events/55-day window. Cheapest build (fifth
  `TribeEventsSource` subclass). Filter decided by maintainer this session:
  **`PuppetMobile` + ALL of `SummerStage`** (not just family-billed shows),
  admin categories out, shared adult blocklist as safety net.
- **NYPL → CONFIRMED**, Incapsula falls; `/events/calendar` is a
  server-rendered Drupal table (~45 rows/page, `?page=N`) with an Audience
  cell per row (Children/Toddlers/Families/…); detail pages carry full
  JSON-LD `Event` incl. branch + borough. Date filter params not yet cracked.
- **QPL → CONFIRMED**, F5 wall falls; Drupal+Solr; listing needs the full nav
  query (`/calendar?searchField=%2A&category=calendar&fromlink=calendar&
  searchFilter=`), pagination via `/search/call?…&pageParam=N` (12
  cards/page); cards carry an audience line; detail pages embed
  `drupalSettings.eventCalendar` JSON.
- **Intrepid → CONFIRMED (endpoint verified)**: real path `/events/calendar`,
  Drupal `views/ajax` POST returns rendered rows with ISO datetimes; pager +
  date-filter params still to map.
- **Macaroni Kid / Yodel → platform cracked**: the widget server-renders a
  JSON-LD `ItemList` (24 events, full addresses+ZIP). Window depth unknown;
  confirmed dedup risk (BPL re-posts seen).
- **AMNH + the Met → deprioritized**: reachable now, but AMNH server-renders
  only ~8 featured cards (Ibexa; full grid is JS, no API found) and the Met
  is a Next.js RSC SPA (data only in the flight payload). Headless-tier.

Probe artifacts (captured pages) are in the job tmp dir, not the repo;
fixture capture for any build must run off-proxy. Implementation plan
presented to the maintainer: build order CPF → NYPL → QPL → Intrepid →
Yodel, with AMNH/Met parked.

### Session (cont'd): backlog source expansion — built Snug Harbor; anti-bot candidates blocked by the web-sandbox proxy

Asked to "add the next top 3 candidates" from SOURCES-BACKLOG.md. Probed the
backlog against what this environment can actually reach and found a hard
constraint: **`curl_cffi` browser-TLS impersonation is broken in the
Claude-Code-on-web sandbox** — the MITM egress proxy connection-resets the
impersonated ClientHello (verified failing even on `example.com`; plain
`httpx` and non-impersonated `curl_cffi` work). So every anti-bot candidate
(the two library systems QPL/NYPL behind WAF/Incapsula — the highest-value
unbuilt leads; AMNH/Met/Intrepid 403; City Parks Foundation Cloudflare)
**can't be fixture-captured here** and must be built from a non-proxied
session. Recorded this in SOURCES-BACKLOG.md's cross-cutting notes.

Of the plain-httpx-reachable candidates, only **one** was a clean, high-value
build, so I built it well rather than forcing three shaky sources:

- **BUILT: `snug_harbor`** (Snug Harbor Cultural Center, Staten Island — the
  catalog's thinnest borough). WordPress custom `event` post type on the plain
  WP REST API, but **the event date lives only in each detail page's JSON-LD
  `Event` node** (`acf` empty, post `date` = creation date), so it lists the
  youth/family events cheaply then crawls each detail page for its date — the
  `mommy_poppins` shape. Kid filter = the `audience` taxonomy (Kids/Families/
  All Ages/Teens) resolved by NAME (survives term-id renumber) and queried as
  an OR filter; shared adult blocklists as a title-scope safety net. All
  taxonomies resolved id→name once/run (`cost-tier`→price, `genre`/`program`→
  tags). Venue/borough hardcoded (single campus) → `SOURCE_NEIGHBORHOOD
  ["snug_harbor"]="Snug Harbor"`. `window_days=60`, opted into
  missing-detection. New module `sources/snug_harbor.py` + registry wiring +
  `_neighborhoods.py` entry; fixture `tests/fixtures/snug_harbor_sample.json`
  (a `terms` block + 12 real `{item, jsonld}` rows) + 22 tests in
  `tests/test_snug_harbor_parse.py` (pure `parse_event`, no httpx mock).
  Verified live end-to-end (147 youth/family events listed, real Staten Island
  rows yielded with correct dates/price/tags). Docs updated: SOURCES-BACKLOG
  (CANDIDATE→BUILT + the "enumerate wp-json for custom post types" lesson),
  README, CLAUDE.md live-sources list. `test_missing_detection` census bumped
  13→14.
- **Deprioritized / rejected (recorded in the backlog):** Brooklyn Bridge
  Parents (WP Event Manager works but only 9 events, and they're re-posts of
  our existing `brooklyn_army_terminal` source — aggregator dedup risk, not
  net-new); Puppetworks (JS-rendered `edit.site` builder — headless-tier);
  NYSCI (Eventbrite-embed, no plain feed); BAM (JS SPA).
- **Anti-bot candidates left for a non-proxied session, with probe findings
  recorded in each source's own backlog entry** (per maintainer request): QPL
  (F5/BIG-IP WAF wall), NYPL (Imperva Incapsula JS-challenge), AMNH/Intrepid/
  City Parks Foundation (403 Cloudflare), the Met (429), BAM (JS SPA). Each
  entry now says exactly what blocked it here and what to try next with
  `curl_cffi impersonate` from a non-proxied session. NYSCI is NOT anti-bot
  (reachable) but delegates to Eventbrite — noted as a different build shape.
  No new neighborhood-coding work was needed for the libraries (the shipped
  `library_neighborhoods.json` already covers all QPL/NYPL branches), so the
  only reusable artifact from this session beyond `snug_harbor` is the probe
  intelligence itself.

Full suite 652 green (was 630), ruff clean.

### Session: fixed the top 3 open issues (#78, #77, #76) — all code-review findings from 2026-07-12

Picked the three most-recently-filed `status:ready` issues, each with a
verified repro and suggested fix already spelled out in the issue body.

- **#78** `brooklyn_army_terminal._parse_start_time`: a time range with an
  omitted start meridiem borrowed pm/am unconditionally from the end of the
  range, so a cross-noon range like `"11:00-2:00pm"` (11 AM-2 PM) parsed the
  start as 23:00 instead of 11:00. Fixed by comparing the borrowed-meridiem
  candidate against the parsed end time and flipping am/pm when the borrow
  would put the start after the end (new `_to_hour24` helper factors out the
  am/pm arithmetic so the flip-check and the final conversion share it).
  Added `"11:00-2:00pm" -> (11, 0)` to the parametrized `_parse_start_time`
  test.
- **#77** `auth._rate_state` unbounded growth: cleanup was purely lazy and
  key-local, so a bucket for a key that's never hit again (e.g. a scanner IP
  that probes `/register` once) lived forever. Added an opportunistic global
  sweep (`_sweep_rate_state`, called every `_SWEEP_INTERVAL` (1000) calls to
  `_bucket_limited` via a module-level counter) that drops any bucket whose
  newest timestamp is older than `_SWEEP_MAX_AGE` (3600s, the largest
  configured window). Hot path stays O(1) amortized; no background task.
  2 new tests (direct sweep eviction; sweep fires opportunistically after
  `_SWEEP_INTERVAL` calls and evicts a stale seeded bucket).
- **#76** `domino_park._occurrence_dates` monthly drift: a monthly series
  anchored on days 29-31 permanently drifted to an earlier day-of-month once
  it crossed a shorter month, because each occurrence was computed from the
  *previous* (already-clamped) occurrence rather than the series anchor —
  Jan 31 -> Feb 28 -> Mar 28 -> Apr 28... instead of Mar 31/Apr 30/May 31.
  Fixed by computing every occurrence as `_add_months(start, i * step)` from
  the anchor via a step index `i`, in both the fast-forward-to-window loop
  and the emission loop, so a clamp in one month never compounds into later
  months. New regression test asserts the Jan 31 anchor case resolves to
  `[Jan 31, Feb 28, Mar 31, Apr 30, May 31, Jun 30]`.

All three repros from the issue bodies re-verified by direct execution
post-fix. Full suite 630 green (was 627), ruff clean. No `db.py`/`server.py`/
`models.py` changes; `auth.py` diff is additive only (new sweep function +
4 lines wired into `_bucket_limited`), so the existing security-baseline
tests are the regression guard for it.

### Session (cont'd): swapped the no-JS neighborhood search for a live JS filter

Maintainer asked what risk the JS alternative (flagged but not built below)
would actually introduce, then asked for it anyway once the tradeoff was
laid out. Added `_NBHD_FILTER_JS` — one static inline `<script>` in
`dashboard.py`, live-filtering `#neighborhood-select` on `input` events
against `#nbhd_q`. The CSP's `script-src` now pins exactly that script's
SHA-256 hash (`_NBHD_FILTER_JS_HASH = "sha256-" + base64(sha256(...))`,
computed from the literal constant at import time — never hand-maintained,
so header and script content cannot drift apart) instead of `'unsafe-inline'`.
Two things keep this narrow rather than opening the door generally:
attacker-influenced content elsewhere on the page (scraped titles/
descriptions/venues) can never execute even under an escaping bug, because
its hash won't match the one pinned entry; and the script's own DOM target
(`neighborhood-select`'s option text) comes from `list_facets()` — curated
static-table labels / Census NTA names, never raw scraped fields — so
there's no path from "attacker controls page content" to "attacker controls
script logic" even in principle. The server-side `_filtered_neighborhoods()`
half from the prior entry is untouched and still does the heavy lifting
(the JS narrows further within whatever the server already rendered;
submitting the form is still how you widen back out to the full list).
Module docstring carries the full reasoning + a note not to add a second
inline script without re-deriving it. 2 new tests (hash matches both the
CSP header and the literal emitted `<script>` text, and targets ids that
actually exist on the page; script absent from non-browse pages) + 1 fixed
(a stale substring assertion after the `<select>` gained an `id` attr).
Verified live: seeded DB, ran the dashboard, headless-Chromium check that
typing "park" narrows the neighborhood `<select>` to "Park Slope" with zero
console errors (confirms the CSP hash isn't blocking the pinned script).
Full suite 626 green, ruff clean.

### Session: human-readable source names + no-JS neighborhood search on the browse page

Maintainer asked for two `/events` browse-page UX fixes: source names
readable instead of internal ids, and a way to search the long neighborhood
list instead of scrolling. Both contained in `dashboard.py` +
`test_dashboard.py`; no db/tools/auth changes.

- **`_SOURCE_LABELS`** (new dict in `dashboard.py`, keyed by `Source.name`)
  + `_source_label()` helper, falling back to the raw internal name for
  anything unmapped (a future source, or a disabled one like
  `nyc_permitted_events`) so a stale/missing entry never breaks rendering.
  Applied everywhere a raw `source` value was shown: the health-page table,
  the browse-page results table, the event-detail page, and the source
  `<select multiple>` (via `_multi_select`'s new `label_fn` param — the
  submitted `value` stays the raw id the filter matches on, only the display
  text changes; option order is now sorted by label, not internal id).
- **Neighborhood search box**, deliberately **not JS** — the dashboard's CSP
  is `default-src 'none'` with no `script-src` at all (module docstring:
  "No JS frameworks... tailnet pages shouldn't leak to third-party hosts"),
  so a live-filter-as-you-type script would mean loosening a header
  CLAUDE.md calls out as intentional. Instead: a `nbhd_q` text field in the
  existing filter form; on submit, `_filtered_neighborhoods()` narrows the
  `<select multiple>` options to a case-insensitive substring match, always
  keeping any already-selected neighborhood in the list even if it no longer
  matches the search text (so refining the search can't silently drop a
  selection out of the form). `nbhd_q` rides into the preset links via
  `_CARRY_PARAMS` like the other single-value filters.
- 4 new tests + 1 updated (source-label mapping/fallback, search narrowing,
  selection preserved across a non-matching search, preset-link carry; the
  existing "every enabled source renders on the health page" test now
  compares against the escaped label instead of the raw id). Full suite 624
  green, ruff clean. Verified live against a seeded DB via curl (label text
  + `nbhd_q=park` narrowing the option list correctly) — no `playwright`
  installed in this sandbox to screenshot, so no headless-Chromium pass this
  time.

### Session (cont'd): retired MULTI-USER-PLAN.md and DASHBOARD-PLAN.md

Maintainer call: both plans are done (multi-user frozen 2026-07-07,
dashboard shipped 2026-07-11) and each already said as much at the top of
its own file ("the living rules are in CLAUDE.md"), so the doc itself was
pure redundancy once the pointer was severed. Swept every reference (~20
across `auth.py`/`config.py`/`dashboard.py`/`db.py`/`users.py`, both test
files, `README.md`, `docker-compose.yml`) rather than just deleting the
files and leaving dead links. Two were load-bearing content, not just
citations, and got folded into their new home instead of dropped:
- **CLAUDE.md "Out-of-scope"**: the multi-user freeze rationale (why no
  further phases) and the dashboard's two design constraints (zero new
  attack surface, read-only by construction) — previously "see the plan
  doc" — are now inline.
- **`db.connect_events_ro` docstring**: the WAL read-only gotcha (why the
  `./data` mount must stay rw) is now explained in place instead of citing
  DASHBOARD-PLAN.md for it.
Left `session-handoff.md`'s own older entries untouched — they're a
historical log of what was true when written, not living docs; rewriting
past entries to match a doc that no longer exists would be pointless
churn. Full suite 620 green, ruff clean.

### Session (cont'd): added `.github/pull_request_template.md`

Maintainer noticed PR #79 had merged early (only 2 of 6 commits made it in
before it closed) and asked for a PR template while sorting that out. The
follow-up commits were rebased onto `main` and reopened as PR #80 (see
below); this commit adds the template itself. Sections: Summary, Changes,
Test plan (suite + ruff + live-verification), Docs (the three-doc convention
— CLAUDE.md / SOURCES-BACKLOG.md / README.md — plus a pointer at the
handoff hook), and a Security surface checkbox calling out the
auth.py/tools.py separation. No code change, no test surface.

### Session: dashboard browse-page UI improvements (branch `claude/ui-search-improvements-ej30h6`)

Maintainer asked for aesthetic + functional improvements to the `/events`
browse page, explicitly directed at a "craigslist minimal" look. All changes
contained in `dashboard.py` + `test_dashboard.py`; no db/tools/auth changes.
Verified end-to-end against a seeded fake DB (seed_fake + live server +
headless Chromium screenshots). Suite 582 green (5 new tests), ruff clean.

- **Restyle (CSS only, still no JS — the CSP has no script-src and that's
  deliberate):** default underlined blue/purple links, hairline
  `border-bottom` row separators instead of full cell borders, no gray form
  box, Arial. The two non-decorative additions: sticky `th` and subtle zebra
  striping (`tr:nth-child(even)` — the header row is child 1, so data rows
  stripe correctly; the `.ok/.warn/.bad` td backgrounds still win over the
  tr-level stripe). Date column `td.when { white-space: nowrap }`.
- **Neighborhood `<datalist>` autocomplete** from `facets["neighborhoods"]`
  (already fetched per-render, previously unused).
- **Date-preset links** (today / this weekend / next 7 days) that carry the
  active non-date filters (`_CARRY_PARAMS`). Weekend math mirrors
  `tools._weekend_window` (duplicated, not imported — the import rule wins,
  same precedent as `_venue_map_url`).
- **"limit reached — more may match" hint** when `len(events) == limit`;
  **reset link** next to the Filter button; **truncated-description tooltip**
  (`title` attr, escaped) on each row's title link.
- Declined by design: tag filter (needs a `db.search` kwarg — noted as a
  possible follow-up), sortable columns/pagination, match highlighting,
  dark mode, pill badges (maintainer wants craigslist-plain).

Sixth commit, same session: **multi-select borough/neighborhood/source
filters + a Source column** on the dashboard browse page (maintainer
request). `db.search`'s `borough`/`source`/`neighborhood` kwargs now accept
`str | Iterable[str] | None` — a string keeps the exact original behavior
(neighborhood stays substring match, used by `tools.search_events` — this
was NOT touched), a list means "any of these": borough/source go through an
`IN (...)` clause, neighborhood switches to EXACT match per selected value
(the multi-select's options are literal `list_facets()` values, not
free text, so substring expansion isn't needed there). Dashboard renders
all three as native `<select multiple>` — no JS, ctrl/cmd-click to pick
more than one, a size floor of 2 rows so a single-option box doesn't look
like a stray number-spinner. `_preset_links` now carries every selected
value via `getlist()` (a plain `.get()` would silently drop all but the
first). New `<td>` for `ev.source` in the results table, header "Source".
13 new tests (7 db.py multi-value, 6 dashboard) verified live against a
seeded DB + headless-Chromium screenshots (union filtering across two
boroughs, selected-state rendering). Full suite 620 green, ruff clean.

Fifth commit, same session: **three new venue sources — the top backlog
candidates reviewed and integrated** (maintainer request: "take the top 3
source candidates, review them and integrate them"). All verified live
before building; each dry-run against the real upstream after building.

- **`si_childrens_museum` (BUILT, ~64 events/60d):** fifth Tribe subclass,
  first real Staten Island coverage. Per-occurrence ids verified live.
  Build-time find: `cost` is always empty — the venue's "Free" *category*
  drives Price.FREE. Curated-kids posture, defensive adult-title net only.
- **`bbg` (BUILT, ~28 events/60d):** Brooklyn Botanic Garden month-page
  scrape (httpx+selectolax). The h2 date header is INSIDE each day's ul as
  first child; family-category allowlist ("Families & Kids" / "Children's
  Garden Classes"); `external_id = slug:date` because drop-in programs
  repeat under every date they run.
- **`brooklyn_bridge_park` (BUILT, ~139 events/60d):** WordPress but NOT
  Tribe — custom `events` CPT on standard WP REST with ACF fields + a
  `maplocations` join for per-pier venues. THE quirk: recurring parents
  AND per-date posts cover the same occurrences → dedup on (base title,
  date), dated post wins. Filter is inclusive+blocklist with TITLE-ONLY
  scope — BBP body text says "parent/guardian who is 18+ must register"
  on Pokémon Day Out, so body-scope adult matching drops exactly the
  wrong rows. Fitness kept only with a family-signal title.
- **WCS zoos REJECTED on yield** (the backlog's own decision gate): 3
  undated season-runs combined across all 5 sites, three sites empty.
  Backlog entry has revisit conditions (re-probe in November for holiday
  lights). Replaced as the batch's third source by Brooklyn Bridge Park,
  whose prior "unreachable" status was a sandbox-egress artifact.
- Missing-detection census 10 → 13 (all three opt in). New fixtures + 25
  parser tests. CLAUDE.md roadmap, SOURCES-BACKLOG as-builts, README
  shipped list all updated (including the stale "tvpp runs side by side"
  README claim left over from the disable).

Fourth commit, same session: **Green-Wood CSS/JS bleed-through fixed in the
shared Tribe `strip_html`** — maintainer reported a description reading
".stk-w5jb2gk {margin-right:0px !important…}". Green-Wood's Stackable theme
embeds `<style>`/`<script>`/`<button>` inside the Tribe `description` HTML;
de-tagging alone left their text content in the prose (the module docstring
had even noted the bleed-through and wrongly claimed the 2000-char trim
handled it — the CSS comes *first*, so it ate the whole preview).
`strip_html` now drops those elements' contents + HTML comments before
tag-stripping, and lstrips the stray leading ", " the empty Tribe schedule
header leaves behind. Fix is shared → all four Tribe sources benefit.
Verified against the live API row the maintainer reported (id 10037316).
**Existing DB rows self-heal**: the nightly upsert rewrites `description`
in place for every row still in the upstream window — no DB surgery needed,
just deploy + one nightly ingest.

Third commit, same session: **time ranges surfaced in listings** — maintainer
reported a noon–4pm Prospect Park event presenting as bare "12". Diagnosis:
the DB had start AND end correct all along (Tribe sources capture `end_dt`);
the listing projection deliberately dropped `end_local` for token efficiency,
so Claude never saw the range. Fix is presentation-only, no ingest changes:
`_event_summary` now includes `end_local` (None when the source has no end),
and the dashboard's When column renders `12:00–16:00` for same-day ranges /
a full second stamp for multi-day. CLAUDE.md "Tool output shape" updated
(the "drops end_local" claim is gone). Reaches claude.ai after the next
image deploy on the NAS.

Second commit, same session: **`nyc_permitted_events` (tvpp-9vvx) DISABLED**
— maintainer said the permit rows go unused now that `nycgovparks_events`
covers the Parks calendar (offered disable / hide-by-default / leave-alone;
they chose disable). Removed from `ENABLED_SOURCES` only; module + parser
tests kept for easy re-enable (steps in the module docstring).
`test_full_window_sources_opt_in` count 11 → 10; CLAUDE.md + backlog notes
updated. **Operator follow-up on the NAS** (optional, keeps the health page
clean — existing permit rows are never flagged missing once the source stops
running): `sqlite3 data/events.db "DELETE FROM events WHERE
source='nyc_permitted_events';"` then rebuild FTS per the db-maintenance
skill if you also VACUUM.

### Session: New York Family source verified AND BUILT (branch `claude/backlog-sources-review-22f37d`)

Backlog review session; maintainer picked **New York Family
(events.newyorkfamily.com)**. Two phases, same session: (1) live
re-verification (first commit, docs only), which found the Schneps network
had deliberately hobbled the Tribe REST API since the 7-06 probe; (2) the
maintainer chose the full day-walk-crawler build over a lossy 16/day version
or parking it, so the source was **built and shipped** as `new_york_family`.
The SOURCES-BACKLOG.md entry (now "✅ BUILT 2026-07-12") holds both the
verification record and the as-built notes — read it before touching this
source. Short version:

- **API quirks the build rides on:** hard 16-row cap (`per_page`/`page`
  ignored; `page>1` returns the same rows as empty husks — the 7-06 "20%
  stubs" explained), `{"events": [...]}`-only envelope, no `utc_*` date
  fields (local `start_date` + `timezone` instead), but
  `start_date`/`end_date` honored with "ongoing at" semantics. So the source
  subclasses `Source` directly (NOT `TribeEventsSource`) and day-walks a
  35-day window with adaptive within-day time slices, deduping on
  `(id, start_date)`. Plain `httpx`.
- **New capabilities for the catalog:** first source with structured age
  bands (category "(N–M)" → `age_min`/`age_max`), strong Manhattan coverage,
  100% of rows arrive with lat/lng. NYC membership by mommy_poppins
  coordinate boxes (city strings are a trap); non-NYC rows (Long Island /
  East End) dropped. `external_id = f"{id}:{start.isoformat()}"` —
  occurrences share the parent id upstream.
- **Numbers:** smoke run 2026-07-11→16: 48 requests → 85 NYC events, 0 dup
  ids; extrapolates to ~280 requests / ~500 events per nightly 35-day run.
  Registered in `ENABLED_SOURCES` before mommy_poppins (slow-crawl block);
  opted INTO missing-detection (window 35 — census test updated to 11
  sources). 18 new parser tests; full suite 577 green, ruff clean.
- **If it breaks:** re-probe the API shape first — it changed in the six
  days between probes; the module docstring documents what to check.

### Session: tailnet dashboard BUILT (branch `claude/ui-feature-planning-build-3w0aup`)

Implemented DASHBOARD-PLAN.md as designed — the plan's prerequisite
(`ingest_runs`, issue #65) had already shipped, so v1 rides the telemetry
table, not `MAX(last_seen)` inference alone. Full suite green, ruff clean.

- **New `src/nyc_events/dashboard.py`** — own Starlette app +
  `python -m nyc_events.dashboard` entry point (uvicorn on
  `config.DASHBOARD_PORT`, default 8766). Routes, all GET-only: `/` (per-
  source health table + catalog strip, `meta refresh` 300s — plan decision 3
  taken), `/events` (filter form mapping 1:1 onto `db.search` kwargs,
  bookmarkable GET params, limit default 50 / cap 200), `/event/{id}`
  (full record + collapsible raw payload), `/healthz`. Stdlib f-string
  templating with `html.escape` on every interpolated value; no JS, no CDN
  assets. Imports `db` + `config` + the sources registry ONLY (never
  `auth`/`tools`); catches `sqlite3.OperationalError` → friendly
  "no database yet" page instead of a 500. Small duplications accepted by
  design (local `_venue_map_url`, `_local_date`) — the import rule wins.
- **`db.py` additions**: `connect_events_ro` (`mode=ro` URI, no DDL —
  read-only enforced at the connection; per the WAL gotcha the mount stays
  rw), `source_health(conn, now, registered)` (union of ENABLED_SOURCES ids —
  passed in, so db.py stays sources-agnostic — and sources present in the DB;
  a registered zero-row source still appears = the "scraper broke" signal;
  joins the latest `ingest_runs` row per source), `catalog_stats`. The 30h
  grace constant moved to its single home `db.MISSING_GRACE_HOURS`;
  `tools._MISSING_GRACE_HOURS` now reads it from there (tools behavior
  unchanged).
- **`config.DASHBOARD_PORT`** (env `DASHBOARD_PORT`, default 8766).
- **Deploy**: `nyc-events-dashboard` compose service (same image, second
  process; **no `env_file`** — never sees `MCP_AUTH_TOKEN`; binds
  `127.0.0.1:8766`; Watchtower label + TCP healthcheck) + dev-override
  build/uid entry. README § "Tailnet dashboard": `tailscale serve --bg
  --https=8766 http://127.0.0.1:8766`, **never funnel 8766**, env-table row.
- **Tests**: `tests/test_dashboard.py` (TestClient: every enabled source
  renders, filters thread through, bad date/age/limit → 400 page, unknown id
  → 404, XSS canary event renders escaped-only on listing + detail, GET-only
  route assertion, POST → 405, missing-DB friendly page) and `test_db.py`
  additions (`source_health` counts/zero-row/unregistered/latest-run join/
  aware-now guard, `catalog_stats`, ro-connection write raises).
- **Docs**: DASHBOARD-PLAN.md marked IMPLEMENTED (kept as design rationale);
  CLAUDE.md Commands + Layout entry (with the import rule) + test-architecture
  line + out-of-scope bullet flipped to "shipped narrow exception".
- **Host actions remaining for the operator** (not repo code): `docker
  compose up -d` to start the new service, run the `tailscale serve` command
  on the NAS, optionally verify `tailscale funnel status` still lists only
  8765.
- **Security-review hardening (same session, follow-up commit)** after a
  branch-diff security pass: (1) scraped event `url`s are now scheme-gated
  (`_safe_url`) before rendering as anchors — a `javascript:`/`data:` URL
  from a compromised feed renders as text, never a clickable link; (2) every
  page now sends the same security-header set as the consent page
  (CSP `default-src 'none'` + `style-src 'unsafe-inline'` +
  `form-action 'self'`, `X-Frame-Options: DENY`, nosniff,
  `Referrer-Policy: no-referrer` — the last also stops the private
  `*.ts.net` hostname leaking to venue sites via Referer), and external
  anchors carry `rel='noopener noreferrer'`; (3) the missing-DB
  `OperationalError` catch narrowed to the two real absent-DB messages so
  other DB failures raise instead of rendering as "no database yet".
  3 new tests (scheme-smuggling canary, headers on every page type,
  unrelated-error re-raise). 556 → 559 passed, ruff clean.
- **LAN exposure opt-in (same session, docs/compose only):** at the user's
  request for their Portainer/NAS deployment, changed the dashboard port
  binding from `127.0.0.1:8766:8766` to `0.0.0.0` (`- "8766:8766"`) so it's
  reachable on the LAN (`http://<nas-ip>:8766`) in addition to
  `tailscale serve` (0.0.0.0 includes loopback, so serve still works). A
  deliberate relaxation of DASHBOARD-PLAN.md's tailnet-only stance — safe
  because the dashboard is read-only, never opens oauth.db, and holds no
  secrets, but it has NO login, so LAN-only-if-trusted. Compose comment +
  README "LAN access (optional)" paragraph document the trade and how to
  revert. The MCP server (8765) stays loopback-bound — unchanged.

### Session: issue-label taxonomy + source-backlog candidate (branch `claude/code-review-bugs-3zzddi`, new PR — the prior PR on this branch, #71, had already merged)

Docs-only, no application code changed. Three commits, rebased onto fresh
`main` after discovering PR #71 (this branch's previous PR) had already
merged before these landed — per the merged-PR-branch convention, kept the
commits and rebased rather than reusing #71 or discarding the work.

- **CLAUDE.md**: new "Issue labeling" section — `type:`
  (bug/data-quality/security/enhancement/chore, pick one), `priority:`
  (P0–P3, pick one), `status:` (triage/ready/in-progress/blocked, open
  issues only), `area:` (auth/sources/db/ingest/tools/infra, multi-select).
  Replaces the old flat labels and title-embedded `[P0]`-style severity.
  Also documents a real tool-gap: the GitHub MCP server here has no label
  create/update/delete endpoint, only `get_label` (read) and
  `issue_write`'s `labels` field, which auto-creates unrecognized names
  with a flat default color and empty description.
- **19 new labels created + applied to all 18 open issues** via the GitHub
  API directly (not part of the repo diff). Full retag mapping and the
  color/description reference table are in the session transcript.
- **`scripts/cleanup_labels.sh`**: one-time admin script (landed via the
  stop-hook's untracked-files auto-commit, then kept — it fits the existing
  `scripts/build_*.py` one-shot-utility convention) that deletes the
  superseded flat labels and sets color/description on the new ones via
  `gh label edit`, closing the cosmetic gap the API-only path couldn't
  reach. Run locally with `gh` authenticated.
- **SOURCES-BACKLOG.md**: added `brooklynbridgeparents.com` as an unprobed
  `CANDIDATE` (Brooklyn-focused family content site, WordPress, dedicated
  `/events/` section) — explicitly disambiguated from the existing
  "Brooklyn Bridge Park" entry (different site: that one's the physical
  park's own calendar). Flagged that events look user/business-submitted
  rather than editorially curated; `source-verifier` is the documented
  next step, not done here.
- Noticed but not acted on: **#60 still open**, looks like a live
  duplicate of the now-closed #35 (same negative-limit bug) — flagged to
  the user, not closed unilaterally mid-labeling-task.
- 532 passing, ruff clean (checked post-rebase).

### Session: ingest telemetry + yield-drift alerting — issue #65 (branch `claude/code-review-bugs-3zzddi`, PR #70)

Implemented #65 (the review's highest-leverage item; guards the silent
per-source decay class that #59 exemplified). 519 → 532 passing, ruff clean.

- **New `ingest_runs` table** in `EVENTS_SCHEMA` (plain `CREATE TABLE`, like
  `geocode_cache`): one row per source per run —
  `run_id`/`source`/`started_at`/`finished_at`/`outcome`/`fetched`/`inserted`/
  `updated`/`marked_missing`/`duration_s`.
- **`db.record_ingest_run`** (writes a row) and **`db.fetch_drift_baseline`**
  (median `fetched` over a source's recent `ok` runs; None until ≥3 exist).
- **`ingest.main`** now records a run on every source exit path (ok /
  fetch_failed / upsert_failed) and, after each ok source, compares `fetched`
  against the prior-runs baseline via `ingest._looks_like_drift` (< 60% =
  drift). Missing-detection control flow was restructured (early-`continue` →
  nested `if`) so recording always happens; behavior otherwise unchanged.
- **New exit code 4** = sources + enrich fine but ≥1 source drifted low.
  Precedence 2 > 3 > 4. Documented in CLAUDE.md ("Ingest exit codes" +
  "Ingest telemetry").
- Tests: `tests/test_ingest_runs.py` — db helpers, the drift predicate, and an
  integration test driving `ingest.main` with a fake source through the
  drift→exit-4 and fetch-failure→exit-2 paths (no network; `ENRICH=0`).
- Deliberately did NOT rewire `_fetch_looks_complete` to use this baseline
  (the issue's side-benefit) — kept scope to the telemetry + alert; noted as a
  follow-up on #65.

### Session: fix the 5 most critical issues (branch `claude/code-review-bugs-3zzddi`, PR #70)

Fixed and tested five issues (each has a new regression test; 504 → 519
passing, ruff clean):

- **#39 (P0)** mommy_poppins time shift. `_parse_local_dt` now reads the
  JSON-LD wall-clock component as America/New_York and ignores the mislabelled
  offset (MP emits both `-04:00` and `+00:00` for the same 10am ET event). The
  captured fixture uses `-04:00` so its instant is unchanged; the bug was the
  live `+00:00` rows landing 4-5h early.
- **#40 (P0)** + **#62** permit/BPL substring gating. `nyc_permitted_events._infer_tags`
  now matches via `_kw_hit` (leading word-boundary prefix; trailing-space
  keyword = whole word), so "craft"≠"aircraft", "sing"≠"closing", "kid"≠"kidney".
  "Shape Up NYC" (adult fitness) added to the title blocklist + dropped from the
  music keywords; "aircraft" added to the blocklist. BPL's title/tags fallback
  now whole-word matches a dedicated `_KID_TITLE_HINTS` set. Residual (noted,
  not fixed): "Fair Housing…" still gets a `festival` tag via the real word
  "fair" — a semantic, not substring, false positive.
- **#59 (P1)** Domino recurrence. `_occurrence_dates` fast-forwards to the first
  in-window occurrence and caps on *emitted* occurrences, so a far-past series
  start no longer exhausts `MAX_OCCURRENCES` walking pre-window dates and
  returning zero.
- **#35 (security)** negative-limit cap bypass. The three listing tools now
  clamp `max(1, min(limit, 50))`.
- **#61 (P3)** whitespace-only query. `db.search` computes the FTS expression
  first and skips the MATCH when it's empty, degrading to a text-unfiltered
  search instead of raising an FTS5 syntax error.

**Deliberately NOT taken: #41 (P0, wrong-borough parks).** A real fix needs
`park_neighborhoods.json` rebuilt with a borough-keyed schema (like the library
table), which requires the Census network — not doable/verifiable in-sandbox.
Left open; it's the top remaining P0.

### Session: full-repo bug review + architectural review (branch `claude/code-review-bugs-3zzddi`)

Two-part review session. **No production code changed** — findings were
recorded as issues + doc-decision edits.

**Part 1 — line-level bug review** of the whole codebase, each finding
verified by executing the code path before filing (suite was green, 504
passed, before and after):

- **#59 (P1)** `domino_park._occurrence_dates`: the `MAX_OCCURRENCES` cap
  counts loop steps from the series' original `startDate`, not emitted
  occurrences — a weekly series started >200 weeks ago (daily: >200 days)
  yields ZERO events, and because domino opts into missing-detection its
  previously-ingested future rows then get falsely flagged
  `possibly_cancelled`. Series age into brokenness silently.
- **#60 (P2)** negative `limit` bypasses the 50 cap (`min(limit, 50)` has no
  floor; SQLite `LIMIT -1` = unlimited → whole catalog in one tool response).
- **#61 (P3)** whitespace-only `query` → empty FTS5 MATCH → OperationalError.
- **#62 (P3)** substring keyword matching on the two GATING filters
  (permit-source `_infer_tags`, bpl `_is_kid_relevant`) admits junk
  ("Closing Ceremony"→music, "Kidney Walk"→best for kids); the word-boundary
  consolidation pass only fixed the non-gating editorial sources, so the
  CLAUDE.md claim was stale. **[Later closed as a duplicate of the older
  #40 (P0); detail folded into #40's thread.]**
- **#63 (P3)** unauth OAuth endpoints 500 on malformed input (non-ASCII
  consent code → compare_digest TypeError; JSON-array /register body;
  non-ASCII PKCE verifier — which also burns the auth code before verify).
- **#64 (P3)** GET /token fully issues tokens with code+verifier in the query
  string; `RedactAuthorizeQueryFilter` only scrubs `/authorize?`.

**Part 2 — architectural review** (strategic pass; full text in the session
transcript). Verdict: core architecture sound, don't rewrite anything; the
dominant risk is **silent per-source data decay managed by prose instead of
instrumentation**. Follow-up issues filed:

- **#65 (P1)** `ingest_runs` table + per-source yield-drift alerting — the
  highest-leverage item anywhere; supersedes DASHBOARD-PLAN's
  "optional/severable" framing (doc updated).
- **#66 (P2)** canonical tag vocabulary in `_filters.py` — spellings have
  fragmented ("arts & crafts" ×5 vs "arts and crafts" ×4, "movie"/"movies");
  land before any new Workstream B source. **[Later closed as a duplicate of
  the older #44 (P1); detail folded into #44's thread.]**
- **#67 (P2)** remove the FTS/VACUUM footgun structurally (explicit INTEGER
  PRIMARY KEY rowid; fallback: startup FTS-integrity probe).
- **#68 (P3)** split ingest into its own compose service (kills the
  Watchtower-restarts-mid-ingest race; settles the PHASE-3 open decision).
- **#69 (P3)** exempt the master bearer from the per-token MCP rate limit
  (batch with #63/#64 into one careful auth.py PR).

**Maintainer decisions recorded (user approved the review's recommendations):**

- **Headless browser STRUCK from Phase 3** — PHASE-3-PLAN.md decision section
  rewritten; re-open only for a specific probed source that demonstrably
  needs rendering. Ingest-image split (#68) proceeds independently.
- **Multi-user FROZEN at the shipped Phases A–C** — freeze note atop
  MULTI-USER-PLAN.md + CLAUDE.md out-of-scope bullet amended.
- **Workstream B ordering: borough-coverage gap is the explicit tiebreaker**
  (7 of 11 live sources are Brooklyn venues) — PHASE-3-PLAN.md Workstream B
  intro amended; tag vocabulary (#44, née #66) is a prerequisite for new sources.
- **Sequencing**: #65 pulled ahead of A2/A3 in PHASE-3-PLAN.md.
- **New "Doc hygiene" section in CLAUDE.md**: one home per fact class
  (docstring > CLAUDE.md > backlog), and session-handoff entries older than
  ~3 sessions may be compressed to one-liners (existing history left intact
  this session — compress opportunistically).

### Session: dashboard design doc (branch `claude/connector-health-dashboard-dex7nx`)

User asked whether a web page showing connector health + event counts, with
self-serve event browsing/filtering, would make sense. Assessment: yes for a
**read-only** page, but only if it never rides the public Funnel hostname or
touches `auth.py` — a browser UI on the MCP server would need session auth on
the do-not-regress surface. Chosen shape (user picked the "read-only app"
flavor, docs-only for now): a separate Starlette process on port 8766, same
image as a second compose service, `events.db` opened `mode=ro`, GET-only,
exposed via **`tailscale serve` (tailnet-only), never `funnel`** — tailnet
membership is the auth.

- **New `DASHBOARD-PLAN.md`** — full design for future work: routes (`/`
  health, `/events` browse mapping 1:1 onto existing `db.search` kwargs,
  `/event/{id}`, `/healthz`), a new `db.source_health()` +
  `db.connect_events_ro()` (dashboard must never call `init_events`), the
  WAL read-only gotcha (don't mount `./data` as `:ro`; enforce at the
  connection), compose service sketch (no `env_file` — the dashboard must
  never see `MCP_AUTH_TOKEN`), test plan (XSS guard on scraped fields,
  GET-only assertion, missing-DB page), and open decisions — the main one
  being an optional `ingest_runs` log table so health stops being inferred
  from `MAX(last_seen)` (severable; v1 ships without it).
- **CLAUDE.md** out-of-scope bullet amended: "Admin UI" now carries the
  planned narrow exception pointing at `DASHBOARD-PLAN.md`.
- **No code changes.** Nothing implemented; suite untouched.

### Session (same branch, new PR — #56 already merged): Phase 3 planning docs updated — A1 closed, A3 weather design settled

Docs-only follow-up after PR #56 merged. Since that PR had already landed,
rebased these two commits onto latest `main` (force-with-lease) rather than
stacking on the old tip:

- **A1 (geocode + neighborhood) marked fully DONE** in `PHASE-3-PLAN.md` and
  `CLAUDE.md`. The only remaining item, `near_me`/distance-from-home, was
  explicitly declined by the maintainer as out of scope — not tracked as
  remaining A1 work. Also caught and fixed a staleness bug: `PHASE-3-PLAN.md`
  still listed Workstream C (tech debt #4-#6) as open, even though
  `CLAUDE.md` already recorded it as closed.
- **A3 (weather) caching design settled**: weather will be keyed by
  `neighborhood` string, not per-event/per-venue coordinates. Rationale: NWS
  forecast grid cells (~2.5km) are already coarser than per-venue precision
  would buy, and a meaningful slice of the catalog gets `neighborhood` from
  the offline enrich tiers (fixed-venue/park/library tables) *without ever
  resolving lat/lng* — coordinate-keyed caching would silently skip those
  rows. Plan: a new one-time `scripts/build_neighborhood_centroids.py` (same
  recipe as the existing `build_*.py` scripts) plus a two-tier cache (stable
  `neighborhood → gridpoint`, short-TTL `gridpoint → forecast`). Events with
  `neighborhood IS NULL` get no weather, consistent with the existing
  graceful-`None` pattern. No code written yet — A2 (indoor/outdoor) is next.

### Session (same branch): nycgovparks_events BUILT — the NYC Parks website source is live

Built the source the two verification sessions below prepped, following the
source-adder recipe and the backlog's build-parameters block as written:

- **New `src/nyc_events/sources/nycgovparks_events.py`** (source
  `nycgovparks_events`, registered in `ENABLED_SOURCES` before
  mommy_poppins): paginates `/events/kids` → `/p2`… until an HTTP-200 page
  with 0 microdata cards (plain `httpx` + browser UA, 1s delay, max_pages=80
  cap), parses the schema.org Event microdata cards with selectolax, and
  joins page 1's `eventsByLocationJSON` blob by detail-URL path for lat/lng +
  the park-property venue name (preferred over the microdata sub-room; blob
  venue is one level above even the "(in …)" parent — "Tudor Park" for
  Addabbo Playground). `external_id` = the per-occurrence numeric id from
  `event_title__<id>` (verified per-occurrence, no compute_id override).
  Skips `CANCELLED:` titles; shared `ADULT_BLOCKLIST` as safety net only (no
  kid filter — Parks-curated category). `window_days=55`, opted IN to
  missing-detection. `raw_payload` = trimmed structured extract, not HTML.
- **Category-id → tag table resolved live** (the spec left tags open): card
  class lists carry `catNN` ids; the id→slug map was solved by intersecting
  class-id sets across `/events` p1–p8 (400 cards, Category-link-line
  constraints) + 10 per-category page probes. 33 ids mapped in
  `_CATEGORY_TAGS` (18=kids, 2=arts-and-crafts, 10=nature, 12=festivals,
  13=film, 25=sports, 47=urbanparkrangers, 303=gardening, …); audience/
  venue-type ids (122 seniors, 205 rec-centers, 206/211/291 internal)
  deliberately unmapped.
- **Tests:** new `tests/test_nycgovparks_events_parse.py` (23 tests against
  the already-captured fixture: blob parse + link join + park-property venue
  preference, happy path, no-join fallback, CANCELLED/adult skips, empty-page
  pagination terminator, tag mapping, DB upsert round-trip).
  `test_missing_detection.py::test_full_window_sources_opt_in` extended (not
  loosened): opted-in census now 10 sources, and this is the first whose
  window isn't 60 (55, mirroring the server's ~55–61-day rolling window).
- **Live smoke test** (2 pages): 100/100 cards parsed, 100/100 joined blob
  lat/lng, all five boroughs, all rows FREE. Expect ~49 pages ≈ **2,430
  events/run** — the largest curated source in the catalog; sanity-check the
  first production ingest's totals. Known residue: ~1% of rows have no
  `addressLocality` and a null blob borough → `borough=None` (they still get
  lat/lng, so enrich tier-5 codes the neighborhood). No age_min/age_max
  (ages are description prose only).
- **Docs:** backlog reassessment section flipped to ✅ BUILT + as-built
  block; CLAUDE.md Live list; README status line, "Why Permitted Events"
  note (now points at the shipped source), and Phase 2 source list.
- Suite **504 passed**, ruff clean. Nothing committed — working tree only.

### Session (same branch): nycgovparks verification CORRECTED — list pages embed full-window JSON with lat/lng

Follow-up source-verifier pass on the commit below (`2a51eab`) found one
material error in it: the "no JSON XHR in the page / lat/lng is detail-only"
conclusion was wrong. **Every `/events/...` list page embeds
`var eventsByLocationJSON = [...]`** (~518 KB on `/events/kids`) — a
map-widget JSON blob carrying the **entire current window** (119 venues ×
2,430 events at probe time), with per-venue `lat`/`lng` (all 119 present),
`borough`, `address`, `accessible`, and per-event `title`, epoch-ms
`startDate`/`endDate`, and the per-occurrence `link` (a perfect join key
against the microdata cards' hrefs). Consequences, applied in place to the
backlog entry's finding 3 + build parameters:

- **Zero geocoding needed for this source** — join the page-1 blob by
  `link` for lat/lng + parent-facility venue name; enrich tier 5 only
  reverse-geocodes for the neighborhood label (cached per coord).
- Only the **full untruncated description** is detail-page-exclusive
  (list snippets are ~185 chars; tool summaries truncate at 200 anyway) —
  detail fetches stay unnecessary.
- Other refinements recorded: pagination terminates on an **HTTP 200 page
  with 0 cards** (p50), not a 404; `window_days = 55` recommended (server
  window is "today → end of next month", 55–61 days); skip rows with a
  `CANCELLED:` title prefix (observed live); cost line `Free!` →
  `Price.FREE`, else `UNKNOWN`.
- Fixture `tests/fixtures/nycgovparks_events_kids_page.html` augmented in
  the working tree with the blob (reduced to its first 6 venues, real
  PHP-style `\/` escaping preserved) and the `parks_pages` pagination
  markup, so the future parser can be tested against both surfaces.

Still no parser/tests/registry code — next step remains `source-adder`.

### Session (same branch): nycgovparks.org/events VERIFIED — ready for source-adder

Ran the source-verifier pass on the nycgovparks.org/events reassessment (the
subagent was killed twice by a session-limit API error; the verification was
finished inline in the main session). All four open questions from the
"Major reassessment" backlog entry are now answered in place — the entry is
flipped to 🟢 CONFIRMED + VERIFIED with full build parameters:

- **Overlap with `tvpp-9vvx`: zero** in a same-day (2026-07-07) comparison —
  the permit registry is third-party field reservations, the website is
  Parks' own programming (Kids in Motion, rec-center camps, ranger events).
  Complementary; build alongside, no dedup.
- **Categories:** ~50 multi-tag slugs; the `kids` tag (cat_id 18) is
  well-applied — kid events found via `nature`/`education` also carried it.
  `/events/kids` alone is the right v1 fetch.
- **Detail fetches NOT needed:** list cards carry a numeric per-occurrence
  event id (`event_title__<id>`), title, URL, ISO start/end with offset,
  venue Place name, borough, ~200-char description, cost, category ids
  (in the card's class list!), and the pearls-pick flag. Only lat/lng +
  full description live on detail pages — deferred to the enrich pass.
  `/events/kids` = 49 pages ≈ 2,430 events, window 2026-07-06 → 08-31.
- **IDs:** distinct numeric id AND dated URL per occurrence of recurring
  programs → `external_id` = the numeric id, no compute_id override.
- No RSS/iCal/JSON alternative (re-checked); no anti-bot (plain httpx + UA;
  robots.txt clean for /events); 49 sequential fetches drew no throttling.

Fixtures captured: `tests/fixtures/nycgovparks_events_kids_page.html`
(p1 trimmed to the events-list container + first 10 cards) and
`tests/fixtures/nycgovparks_event_detail.html` (full page, has lat/lng).
No parser/tests/registry written — that's the next step, via `source-adder`,
using the build-parameters block in the backlog entry.

### Session: backlog expansion — 6 new candidates (branch `claude/backlog-sources-integration-axzlam`)

Added six new CANDIDATE entries to `SOURCES-BACKLOG.md` at the user's request,
all unprobed:

- **Brooklyn Museum** — fine-arts museum (First Saturdays, Brooklyn Museum
  Kids); flagged explicitly as distinct from the already-BUILT
  `bk_childrens_museum` source (added a note at the top of the "Candidates"
  section to prevent that mix-up going forward).
- **New York Hall of Science (NYSCI)** — Queens, likely a curated-kids-feed
  shape (little/no filter needed) like `mommy_poppins`/`bk_childrens_museum`.
- **American Museum of Natural History (AMNH)** — Manhattan, Upper West Side;
  needs a family-strand filter (Discovery Room, sleepovers) over an adult
  member-lecture/gala calendar.
- **Intrepid Sea, Air & Space Museum** — Manhattan, Hell's Kitchen; family
  camps/STEM days vs. private evening rentals.
- **City Parks Foundation** (SummerStage, Puppet Mobile, Charlie Parker Jazz
  Fest) — citywide multi-park aggregator, closest in shape to the permit
  source but editorially curated. Flagged as needing **per-event
  venue/borough** (like the NYPL requirement) since it spans many parks
  across boroughs, plus a real filter strategy decision (Puppet Mobile is
  all-ages; SummerStage skews adult/ticketed).
- **Gothamist** — flagged as the **weakest candidate**, likely REJECTED on
  first probe: its kids content is almost certainly digest/roundup articles
  ("32 things to do with kids this weekend"), same free-text-extraction
  problem already identified for The Skint (out of scope per
  PHASE-3-PLAN.md). Recommended probing it first/fast specifically to settle
  that question rather than investing more research time.

The Whitney and The Skint were already in the backlog from a prior session —
not duplicated. No code changes; docs only. Next step for any of these is
`source-verifier`.

### Session (same branch): major finding — nycgovparks.org/events is alive and much richer than tvpp-9vvx

User asked why we use the permit registry (`tvpp-9vvx`) instead of
`nycgovparks.org/events` for Parks events. Answer required digging into the
history (`nyc_permitted_events.py` docstring + README): the original Phase 1
spec named the NYC Parks Events Listing **Open Data dataset** (`fudw-fgrp`),
found it frozen since 2019-12, and pivoted to `tvpp-9vvx` as the "live
successor" — but that investigation only ever checked the Open Data catalog,
never the live website itself (a separate system from its old Socrata
mirror).

Live re-probe (`httpx`, no anti-bot) found `nycgovparks.org/events` **very
much alive** — 10,964 events listed out to March 2029 — with real
schema.org `Event` microdata: descriptions (100% of a 50-row sample; the
permit source has zero), full ISO+offset start/end times, borough +street
address, and an NYC-Parks-curated **`kids` category** ("Best for Kids",
directly URL-addressable at `/events/kids`, 2,427 events in a ~56-day
window with zero client-side filtering needed). Detail pages additionally
carry **lat/lng directly** — zero enrich-pass geocoding needed for these
rows, unlike every other venue source. This is a substantially richer,
more curated source than the noisy permit registry currently powering
Phase 1.

Wrote this up as a prominent "🔴 Major reassessment" section at the top of
`SOURCES-BACKLOG.md` (not buried as a routine candidate) with the full
findings, a pagination note (`/events/kids/p2`, path-based not query-param),
and four open questions before committing: overlap with `tvpp-9vvx` (may be
complementary — rec-center programming vs. permitted third-party events —
unconfirmed), full category vocabulary (does kid-relevant content hide
outside the `kids` tag, like Green-Wood/Prospect Park's category patterns?),
whether per-event detail fetches for lat/lng are worth it at ~2,400
rows/window, and site stability under a ~49-page nightly crawl. Also added
a pointer note in README's "Why Permitted Events and not Parks" section so
this doesn't get silently re-assumed stale. Recommended `source-verifier`
next, given the potential upside. No code changes — probe + docs only.

### Session (same branch): 8 new candidates probed live — 3 ready to build, 1 multi-site bonus, 4 open

Probed all 8 sources the user asked to add (live `httpx` fetches, no
speculation) and wrote full findings into `SOURCES-BACKLOG.md`. Sorted by
outcome:

- **🟢 Staten Island Children's Museum — CONFIRMED, ready for `source-adder`
  today.** Real Tribe/WordPress REST API (`sichildrensmuseum.org/wp-json/
  tribe/events/v1/events`, 51 upcoming events, single venue). Highest-value
  find — Staten Island has near-zero coverage today. Fifth `_tribe.py`
  subclass, no new machinery needed.
- **🟢 Brooklyn Botanic Garden — CONFIRMED, HTML scrape (BAT-style).**
  `bbg.org/visit/calendar` is clean server-rendered HTML with a real
  `event-tag` category field (e.g. "Children's Garden Classes") — filterable
  by category, not keyword-guessing.
- **🟡 New York Family (events.newyorkfamily.com) — CONFIRMED feed, geo-filter
  required.** Sixth Tribe instance, but it's a *regional* parenting calendar
  (found live venues in Huntington Station, Southampton — Long Island, not
  NYC). Needs a `venue.city` allowlist + drop-if-missing-city rule before
  building; also has a data gotcha (~20% of returned events are bare stub
  objects missing `id`/`title` — must be skipped). Real upside: actual
  age-band categories ("Kids (5–8)") — first source with structured age data
  if built.
- **🟡 Bronx Zoo — CONFIRMED but sparse (2 items live); found a 5-site bonus.**
  Same WCS route (`/things-to-do/events`) confirmed live on Central Park
  Zoo, Prospect Park Zoo, Queens Zoo, and NY Aquarium too — one scraper class
  could cover all 5. Worth checking combined yield before committing;
  individually each may be too thin to justify.
- **Macaroni Kid (Brooklyn NW + Lower Manhattan) — platform identified
  (Yodel widget, `events.yodel.today`), franchise-network shape like
  NYPL/QPL, but the widget host Cloudflare-challenged this session's fetch
  attempts (both plain `httpx` and `curl_cffi`, the latter connection-reset
  same as other blocked hosts this session). Needs a retry from a different
  network, not a rejection.
- **NYBG — dead end on the obvious path.** No Tribe/event REST routes exist
  (196 routes checked); `/events/` is a marketing page, not a calendar.
  Family programs likely live on a separate ticketing subdomain not found
  this session — flagged what NOT to re-check.
- **Snug Harbor — inconclusive**, no platform signature matched (the
  "algolia" lead was a false positive — just search-UI CSS).
- **Bronx River Alliance — thin**, the events page renders almost no content
  statically; deprioritized.

No code changes — probes + backlog writeup only. Recommended next action:
hand Staten Island Children's Museum and Brooklyn Botanic Garden straight to
`source-adder`; run `source-verifier` on New York Family (geo-filter design)
and the WCS zoos (combined-yield check) before building those two.

### Session (same branch): Time Out re-probed — rejection stands, reason updated

Re-assessed the Time Out NY Kids rejection at the user's request (the
"non-impersonating probe" lesson made the old verdict suspect). Live re-probe
2026-07-06, plain `httpx` (no anti-bot):

- The old reason is **stale**: the site is server-rendered now, not
  JS-rendered. No headless browser needed to read it.
- But the rejection **stands** on new grounds: the kids vertical has zero
  dated events (evergreen listicles only), and while the main NYC monthly
  events calendar (~58 items/month, server-rendered, detail pages with
  structured Address/Price/Opening-hours box and a `TheaterEvent`-typed
  JSON-LD) is real, **no `startDate` exists anywhere** — event dates are
  mid-sentence editorial prose, which is free-text NLP extraction, out of
  scope per PHASE-3-PLAN.md. Kid yield of the general calendar was ~5% on a
  quick `_filters.py` pass anyway.
- Rewrote the SOURCES-BACKLOG.md Rejected entry with the re-probe findings
  and a concrete revisit tell (watch for `startDate` appearing in the
  JSON-LD — the CMS already types events, it's one field away). Stub
  tombstone unchanged. Docs only, no code.

### Session (same branch): The Skint probed in depth — digest parser is buildable, yield is the open question

Followed up on "is there a way to make The Skint work" by actually fetching
the live feed (plain `httpx` — `curl_cffi` got connection-reset from this
sandbox, the reverse of the usual pattern) and parsing all 19 available posts.
Rewrote the `SOURCES-BACKLOG.md` entry from speculative to probed. Key
findings:

- **Item granularity resolved:** 8/19 posts are digest/roundup posts (the
  bulk of the value), 11/19 are standalone "(SPONSORED)" ad placements
  (mostly adult, unstructured dates) — recommend skipping standalone posts
  entirely and only parsing digests.
- **Digest format IS templated, not free prose** — confirmed a regex matches
  239 of 472 `<p>` blocks across the 8 digests as `<time-phrase>: <b>title</b>:
  description`. This is deterministic text parsing, not the AI/NLP extraction
  PHASE-3-PLAN.md rules out — but it needs a small state machine (day-header
  segmentation + folding ~40% multi-paragraph continuation blocks into the
  prior event) and a separate "ongoing events roundup" blurb deliberately
  skipped (no per-item dates).
- **Venue extraction better than expected:** ~50% of events end with a
  `Venue (neighborhood)` clause (e.g. "halyards (gowanus)") that could map
  onto existing NTA labels via a small alias table — no geocoding needed for
  those rows.
- **Kid yield is the real gating number:** ran the actual shared filter
  (`_filters.py`) plus a draft allowlist against all 239 parsed events — **14
  kept (5.9%)**, roughly 3–5 truly distinct kid-relevant events/week after
  dedup (several hits were the same recurring "Free Outdoor Movies" series
  counted once per day-header). Above Coney Island USA's ~2% rejection floor,
  well below the built park/museum sources' density.
- **Not built.** This is a maintainer call: the parser is real work (messiest
  in the codebase) for a modest yield. Full findings + the concrete parser
  sketch are in the backlog entry. No code changes this session — probe +
  docs only.

### Session: invited-user onboarding in README (branch `claude/add-puppetworks-source-wup5yq`)

Added README § "Onboarding an invited user" under the connector docs. The
existing "Inviting friends & family" section was operator-facing (the `users`
CLI); this fills the gap with a copy-pasteable, jargon-free walkthrough the
operator can forward to the invited person verbatim — Settings → Connectors →
Add custom connector, paste URL, paste invite code on the approval page, done
— plus keep-your-code / invalid-code / bad-URL troubleshooting and a note that
codes are reissued (never recovered) via revoke+add. Docs only; no code, no
test changes.

### Session: multi-user Phase C documented (branch `claude/add-puppetworks-source-wup5yq`)

Phase C of `MULTI-USER-PLAN.md` is ops, not code — the repo side is a
runbook. Added README § "5. Backups + uptime monitoring (multi-user
Phase C)" under Deploy:

- Nightly `oauth.db` snapshot via SQLite's online backup API through the
  container's Python (`docker exec nyc-events python -c "...s.backup(d)..."`
  → `/data/oauth.db.bak`), because a raw file copy of a live WAL DB risks a
  torn copy. One-liner verified locally against a WAL db. `events.db`
  deliberately not backed up (ingest rebuilds it).
- External monitor on the PUBLIC Funnel `/healthz` (200 "ok"), so the check
  exercises Funnel + container; recommends an off-NAS pinger over
  same-NAS Uptime Kuma (shared failure domain). No token in monitor config.
- Keep-single-worker stance already enforced/documented — box ticked.

Plan checkboxes ticked with a "repo side DONE, NAS actions pending" status
note. Remaining NAS actions for the operator: create the DSM Task Scheduler
backup job, ensure `./data` is in a Hyper Backup task, point a monitor at
the Funnel URL. With this, all three phases of MULTI-USER-PLAN.md are
closed on the repo side.

### Session: multi-user Phase B implemented (branch `claude/add-puppetworks-source-wup5yq`)

Implemented Phase B of `MULTI-USER-PLAN.md` (hardening), same session as
Phase A:

- **Tokens hashed at rest** — `db.hash_access_token()` (`sha256:<hex>`
  prefix); `store_oauth_token` stores the hash, `is_valid_oauth_token`
  hashes the presented bearer before lookup. `_migrate_oauth` rewrites any
  legacy plaintext row in place once (prefix makes it idempotent; clients'
  cached plaintext bearers keep working). Plain SHA-256 on purpose — tokens
  are 384-bit random, PBKDF2 stays reserved for human-carried invite codes.
- **Per-token rate limit on the MCP path** — `_MCP_TOKEN_LIMIT = (60, 60)`
  applied in `BearerAuthMiddleware` after successful auth (master bearer
  included). Rate-limiter core refactored into `_bucket_limited()` shared
  with the per-IP OAuth-endpoint limiter; buckets keyed by token sha256,
  never the raw bearer.
- **Access-log redaction** — `auth.RedactAuthorizeQueryFilter` rewrites
  `/authorize?...` → `/authorize?[redacted]` in `uvicorn.access` records;
  wired in `server.main()`. Closes the "no persistent log scrubbing"
  accepted residual in CLAUDE.md.
- Tests: 7 new (hashing at rest, plaintext-migration idempotency, per-token
  limit semantics, no-raw-token-in-bucket-keys, log-filter scrub/pass-through);
  two Phase A tests updated to look rows up by hash. Suite 481 passed, ruff
  clean. Docs: CLAUDE.md baseline + residuals, README, plan checkboxes.

Phase C (oauth.db backup, uptime check) remains open — it's ops work on the
NAS, not repo code, except the documented keep-single-worker stance.

### Session: multi-user Phase A implemented (branch `claude/add-puppetworks-source-wup5yq`)

Implemented Phase A of `MULTI-USER-PLAN.md` (per-person credentials):

- `db.py`: new `users` table in `OAUTH_SCHEMA` (`user_id`, unique `name`,
  `passcode_hash`, `created_at`, `revoked_at` tombstone); `_migrate_oauth`
  adds `oauth_tokens.user_id` (idempotent column-add, `expires_at` pattern);
  `store_oauth_token` takes `user_id`; new `create_user` /
  `get_user_by_name` / `active_user_passcodes` / `revoke_user` (tombstone +
  delete their tokens, returns count) / `list_users` (with token counts).
- New `users.py`: PBKDF2-SHA256 salted passcode hashing (codes are generated
  `token_urlsafe(24)`, hash-only at rest), `match_user()` (checks every
  active hash, no early exit), and the `add`/`revoke`/`list` CLI
  (`python -m nyc_events.users`; `add` prints the code exactly once).
- `oauth.py`: `AuthCode` gains `user_id`; `issue_auth_code` threads it.
- `auth.py`: `authorize_post` accepts the operator consent password OR a
  user invite code (DB lookup only on password miss); matched `user_id`
  rides the auth code and is stamped onto the token at `/token`. Consent
  label "Master token" → "Access code". No other auth surface touched.
- Tests: new Phase A section in `test_security_fixes.py` (17 tests —
  migration, hashing, matching, revocation semantics, full
  authorize→token attribution flow via real Starlette requests, CLI).
  Full suite 474 passed, ruff clean.
- Docs: CLAUDE.md (commands, layout, OAuth model, out-of-scope reworded to
  multi-*tenancy*), README (invite flow + rotation model), plan checkboxes
  ticked with SHIPPED marker.

NOT done (deliberate): Phase B (hash tokens at rest, per-token rate limit,
log residual) and Phase C — still open in `MULTI-USER-PLAN.md`. Deploy note:
run the CLI inside the container (`docker exec ... python -m
nyc_events.users add <name>`) so it hits `/data/oauth.db`.

### Session: multi-user plan doc (branch `claude/add-puppetworks-source-wup5yq`)

Wrote `MULTI-USER-PLAN.md` — the roadmap for opening the server to a small
friends-and-family circle. Key framing: the data is shared/read-only, so no
tenancy work; all changes are auth-layer. Phase A (per-person invite codes in
a `users` table, `user_id` attribution on `oauth_tokens`, a `users` admin
CLI) must ship before inviting anyone; Phase B is hardening (tokens hashed at
rest, per-token rate limit on POST /, log residual re-check); Phase C is
availability guardrails (oauth.db backup, /healthz uptime monitor, keep
single-worker). Maintainer explicitly dropped the shorter-TTL item
(lost-device window not a concern). Plan doc only — no code changes yet;
CLAUDE.md's "out-of-scope: multi-user" line gets updated when Phase A ships.

### Session: Puppetworks + Brooklyn Bridge Park backlog entries (branch `claude/add-puppetworks-source-wup5yq`)

Added Puppetworks (marionette theater) and Brooklyn Bridge Park to
`SOURCES-BACKLOG.md` as CANDIDATEs under "Candidates — to probe". Live
probing was attempted from this session (`curl_cffi impersonate="chrome"`)
for both `puppetworks.org` and `brooklynbridgepark.org`, but every request
got `Recv failure: Connection reset by peer` — sandbox egress to both hosts
is currently blocked, so both entries are unprobed. Flagged that Puppetworks
is a distinct venue from Brooklyn Bridge Park itself (historically Park
Slope, not part of the park) so the two shouldn't be conflated when built.
No code changes; next step for both is `source-verifier` once the hosts are
reachable (retry from a different network per the "sandbox egress varies"
note).

### Session: security fixes #33/#34/#36 (branch `claude/security-issues-review-q0w33m`)

Handled the High + Medium findings from the 2026-07-05 security review
(issues #33, #34, #36). Lows #35/#37 deliberately left open.

- [x] **#33 (High) — rate-limiter bypass via spoofed `X-Forwarded-For`:**
      `docker-compose.yml` no longer sets `FORWARDED_ALLOW_IPS: "*"`. The
      compose file now pins the bridge network (`172.28.0.0/24`, gateway
      `172.28.0.1`) and trusts exactly the gateway, so uvicorn walks XFF
      right-to-left past the trusted hop to the Funnel-appended real client
      IP instead of taking an attacker-supplied leftmost entry. CLAUDE.md
      quirk #5 rewritten (it used to claim the wildcard was safe — that
      reasoning only covered hostname forgery, not rate-limit keying);
      security-baseline bullet + README env table updated.
      **Deploy note:** `docker compose up` will recreate the network; verify
      after deploy that OAuth discovery still advertises `https://…` (the
      original reason `*` was chosen) — if the NAS assigns a different
      gateway, adjust subnet + `FORWARDED_ALLOW_IPS` together.
- [x] **#34 (Medium) — no body cap on unauthenticated OAuth endpoints:**
      `auth._body_too_large()` enforces an 8 KB cap (`_MAX_BODY_BYTES`) on
      `/register`, `/authorize` POST, `/token`, after the rate-limit check
      and before any parse. Content-Length is a cheap first reject, but the
      cap binds while draining the stream, so chunked bodies without
      Content-Length can't bypass it; the drained body is cached on the
      request so Starlette's `.json()`/`.form()` still work. 6 new tests in
      `test_security_fixes.py` (oversized declared / oversized streamed /
      within-limit-still-parses for register; oversized for authorize+token;
      undersized token still reaches grant validation).
- [x] **#36 (Medium) — supply chain:** Watchtower pinned
      `containrrr/watchtower:latest` → `:1.7.1` (it holds the Docker
      socket; it must not float). New `requirements.lock` (fresh-venv
      resolve of the runtime deps, no hashes — multi-arch builds would need
      per-platform wheel digests); Dockerfile installs the lock first, then
      the project `--no-deps`, so image builds stop floating on
      newest-PyPI. Full suite verified green against the pinned set in a
      clean venv (455/455 pre-change, 461/461 after). pyproject keeps loose
      floors for dev. **Residual (deliberate, maintainer's call):** the app
      image itself stays on `:latest` + Watchtower auto-deploy — that's the
      designed update path; pinning it would break auto-updates. README
      documents the trade and the manual-pull alternative.
- [x] Suite **461 passed** (455 + 6 new), ruff clean.

### Session: scaffolding & docs drift review (branch `claude/scaffold-docs-review-3jtjpi`)

Reviewed the repo's AI scaffolding (`.claude/` agents/skills/hooks, CLAUDE.md,
MCP tool docstrings, docs) for staleness and gaps. Verdict: the scaffolding
itself is in good shape (agents/skills/hooks verified against code and history;
tool docstrings verified against `db.search` semantics — no changes needed
there beyond one nit). The problem was **status drift across four surfaces**,
all fixed this session:

- [x] **Deleted `progress.md` + `feature-list.json`** — the harness-template
      trackers were stale and mutually contradictory (feature-list said
      feat-006/008/009 "not-started"; progress.md said feat-006/009 shipped;
      CLAUDE.md — the declared canonical source — says A1 done, tech debt
      closed). CLAUDE.md `## Phase roadmap` + this handoff now carry those
      roles alone. `init.sh` next-steps repointed accordingly.
- [x] **CLAUDE.md de-staled:** removed the two references to the deleted
      `FILTER-REVIEW.md` (here + SOURCES-BACKLOG.md); fixed the Docker
      healthcheck bullet that implied `/healthz` doesn't exist (it does —
      `server.py`); trimmed the four long Phase-2 as-built paragraphs
      (BAT / Industry City / Governors Island / Domino Park) to one-liners
      keeping only the load-bearing gotchas — the full notes stay in
      SOURCES-BACKLOG.md as-built blocks; clarified the Phase-3 TODO line
      (tech-debt #4/#5/#6 closed).
- [x] **README de-staled:** Phase 3 status now says the enrichment pass
      shipped (it said "planned (not yet implemented)" while the deploy
      section described the pass running); project-layout tree updated for
      the post-#26 server split (auth/tools/config/oauth/enrich/geocode
      modules) and de-drifted (per-source file list replaced with a pointer
      to CLAUDE.md `## Layout`); "6 tools" → 7; the "Why Permitted Events"
      source enumeration (7 of 10 listed) replaced with a pointer to Status.
- [x] **`list_sources` docstring** (tools.py) now steers consuming LLMs to
      `list_facets` for filter values — it's a health tool, not a search tool.
- [x] **ingest-health skill:** replaced hard `file.py:NN-NN` line references
      (already rotted) with symbol references.
- [x] Full suite green + ruff clean after the tools.py docstring change.

Explicitly NOT done (reviewed and rejected as premature): new slash commands,
new agents, ADR files, tool-description rewrites beyond the one nit above.

### Session: neighborhood persistence, issue #27 (branch `claude/architecture-design-review-8r5735`, same session as #26/#25 below)

Fixed the wipe-and-restore fragility: the nightly upsert used to null every
row's `neighborhood` and rely on a best-effort enrich pass to restore it, so
one failed pass left the whole catalog without neighborhoods (and
`search_events(neighborhood=...)` returning nothing) for 24h, silently.
Implemented issue #27's option 1 + the option-2 exit code.

- [x] **Upsert preserves enrichment** (`db.upsert_events`): `neighborhood`/
      `lat`/`lng` now use CASE expressions — a source-provided value wins;
      otherwise the enriched value is kept; and the coding resets to NULL
      exactly when the row's **venue or borough changed** this ingest
      (null-safe `IS NOT`), so stale coding re-resolves the same night.
      That last clause handles the staleness objection to plain COALESCE
      (an event moved to a new venue no longer keeps the old venue's label).
- [x] **`enrich --recode-all`** — new CLI flag / `run(recode_all=True)`:
      re-resolves every row (not just `neighborhood IS NULL`); needed now
      that static-table corrections no longer propagate via the nightly
      wipe. Conservative: a row whose re-resolution fails keeps its old
      label (recode only adds/updates, never removes). `allow_abbrev=False`
      so `--recode` fails loudly instead of silently matching (caught live
      during verification).
- [x] **Ingest exit code 3** when the enrich pass raises (sources still
      commit first; source failures keep exit 2, which takes precedence) —
      the DSM cron can now alert instead of the failure landing in stderr
      of a 0-exit run.
- [x] **Tests** — 4 new upsert-persistence cases in `test_db.py` (preserve
      on re-ingest / source wins / venue change resets / borough change
      resets), 2 new recode cases in `test_enrich.py` (reprocesses coded
      rows; keeps label on failed resolution). **455 passed, ruff clean.**
- [x] **Runtime-verified via the real CLIs** on a seeded temp DB: nightly
      enrich coded only the NULL row (offline park tier), second run 0/0,
      `--recode-all` re-resolved all 6 rows through the live Census
      geocoder (5 misses kept their labels, 1 stale label recoded), second
      recode served entirely from the negative cache (0 HTTP requests),
      re-seed (UPDATE path) blanked nothing.
- [x] **Docs** — CLAUDE.md: Commands (+`--recode-all`), the "Persistence"
      paragraph replaces "Why re-running nightly is cheap", ingest exit
      codes (0/2/3), egress-debt note updated (existing rows keep labels;
      blocked-geocoder misses are cached as negatives — check
      `geocode_cache` when debugging).

### Session: server.py split, issue #26 (branch `claude/architecture-design-review-8r5735`, same session as #25 below)

Split the 926-line `server.py` on churn vs consequence, per issue #26. Pure
move — no handler/middleware logic changed (one attempted "improvement" to
the middleware style was caught and reverted mid-session; the security
surface ships byte-equivalent logic).

- [x] **`auth.py`** (new) — the "do not regress" surface: rate limiter +
      buckets, OAuth token cache, `BearerAuthMiddleware`, redirect-URI
      allowlist, discovery endpoints, `/register`, `/authorize` GET/POST,
      `/token`, consent HTML + security headers. Module docstring carries the
      single-process warning (issue #30 item 1); CLAUDE.md security baseline
      gained a matching **single-worker only** bullet.
- [x] **`tools.py`** (new) — the MCP surface: `FastMCP` instance, all seven
      tools, `_event_summary`/`_event_detail`, `_weekend_window`,
      `_normalize_borough`, `_local_date`, `_venue_map_url`,
      `_possibly_cancelled`.
- [x] **`config.py`** (new, issue #30 item 2) — env-derived settings read
      once: `DB_PATH` (was read in **four** places: server/ingest/enrich/
      seed_fake — all now `config.DB_PATH`), `OAUTH_DB_PATH`, `PORT`,
      `FORWARDED_ALLOW_IPS`, `OAUTH_TOKEN_TTL_DAYS`, redirect allowlist.
      Consumers use attribute access so tests monkeypatch `config.X`.
      Credentials deliberately stay call-time env reads (master token never
      sits in an importable module attribute).
- [x] **`server.py`** now 97 lines: `build_app()` + `main()` only.
- [x] **Tests repointed** (import/monkeypatch targets only, no assertion
      changes): `test_security_fixes` → `auth`, `test_search_tools` →
      `tools` + `config.DB_PATH`, `test_event_projection` /
      `test_weekend_window` / `test_missing_detection` → `tools`.
- [x] **Runtime-verified end-to-end** (booted the real server on a temp DB):
      browser probe 200 / POST 401, discovery JSON, consent page + all
      security headers, evil-redirect 400, full OAuth flow (register →
      consent with separate consent-pw AND master fallback → PKCE exchange →
      issued bearer accepted), auth-code single-use, MCP protocol round trip
      (initialize → tools/list shows all 7 → tools/call returns seeded rows),
      rate limiter 429s at request 6 with Retry-After, GET /token downgrade
      guard 400s. **449 passed, ruff clean.**
- [x] **Docs** — CLAUDE.md Layout (four module entries replace the server.py
      line; the ">600 lines → split" paragraph replaced by "never blend them
      back"); security baseline gained the single-worker bullet.

### Session: Tribe source consolidation, issue #25 (branch `claude/architecture-design-review-8r5735`)

Architecture-review session: filed issues #25–#30 from a full design review,
then implemented **#25** — the four WordPress / The Events Calendar (Tribe)
sources were ~150-line copies of each other and had already drifted.

- [x] **New `src/nyc_events/sources/_tribe.py`** — everything that is a
      property of the *plugin*, not the venue: `TribeEventsSource` (the
      fetch/pagination loop + curl_cffi Chrome-impersonation page fetch),
      `parse_row`/`RowParts` (the common row skeleton: kid-relevance gate,
      title, UTC dates, per-occurrence external_id, excerpt-preferred
      description + 2000-char trim, raw_payload), and the canonical
      `strip_html` / `parse_utc_dt` / `parse_cost` / `category_names`.
- [x] **Four sources rewritten as subclasses** — `greenwood_cemetery`,
      `prospect_park`, `industry_city`, `ny_transit_museum` now keep only
      venue-specific logic: filter strategy, tag rules, venue/borough/price
      mapping (incl. NY Transit's venue-object mapping + "Included with
      Museum admission"→PAID override, Industry City's always-UNKNOWN price).
      Each keeps a module-level `_parse_row` (assigned into the class via
      `staticmethod`) plus `_strip_html`/`_parse_utc_dt`/`_parse_cost` aliases
      so the parser tests exercise them unchanged. **Net −634 lines.**
- [x] **Drift fixed: entity decoding unified on `html.unescape`.**
      Prospect Park / Industry City / NY Transit hand-replaced a fixed handful
      of entities (Green-Wood already used unescape). Behavior change:
      `&#8217;` now decodes to the real `’` (U+2019), not a normalized ASCII
      `'` — three test assertions updated to the faithful decode. Event ids
      are unaffected (all four sources have per-occurrence external_ids).
- [x] **`window_days` double-duty collapsed** (issue #30 item 3, Tribe
      sources only): one attribute, set once in the base `__init__`; the
      `self._window_days`/`self.window_days` duplication is gone. Base opts
      into missing-detection by default (all Tribe sources are full-window).
- [x] **Smoke-tested beyond the suite:** all four classes instantiate via the
      `ENABLED_SOURCES` no-arg path, and the shared `fetch()` loop yields
      correct events end-to-end against stubbed pages from the fixtures.
- [x] **Docs** — CLAUDE.md Layout (new `_tribe.py` entry: subclass, never
      copy-adapt), `.claude/agents/source-adder.md` Tribe fast-path now
      points at `TribeEventsSource`. **449 passed, ruff clean.**

### Session: MCP tool filters + facet discovery (branch `claude/mcp-tools-review-6unjn8`)

Reviewed the MCP tool surface and implemented three of the suggested gaps. No
schema/migration changes — all additive filtering over the existing `db.search`.

- [x] **`exclude_low_confidence` filter** — new bool on `db.search` (`description
      IS NOT NULL OR url IS NOT NULL`) and exposed on all three listing tools
      (`search_events`, `events_this_weekend`, `events_on_date`). Drops permit-
      style rows for the "only curated, attendable events" path; mirrors the
      existing `low_confidence` output flag. Fixes browse tools being flooded by
      the ~700-row permit source.
- [x] **Arbitrary date window on `search_events`** — `start_date`/`end_date`
      (YYYY-MM-DD, NYC local). `start_date` defaults the window start to that
      date instead of now; `end_date` omitted → `start + days_ahead` (same
      precise-instant semantics as the existing now-window). End-before-start
      and bad formats raise `ValueError`. Shared `_local_date()` helper (also
      now used by `events_on_date`).
- [x] **`source` filter on `search_events`** — restrict to one source id.
- [x] **`list_facets()` new tool** — distinct in-catalog `boroughs`,
      `neighborhoods`, `tags`, `sources` so a caller can discover valid filter
      values. `db.list_facets()`; tags unpacked from per-row JSON in Python (no
      json1 dependency).
- [x] **`search_events` default `limit` 10 → 15** (others stay 10; all cap 50).
- [x] **Tests** — `test_db.py` (source filter, exclude_low_confidence, two
      `list_facets` cases); new `test_search_tools.py` (date-range window math,
      end-before-start guard, bad-format guard, exclude_low_confidence + facets
      through the tool layer, via monkeypatched `server.DB_PATH`). **449 passed,
      ruff clean.**
- [x] **Docs** — CLAUDE.md "Tool output shape" (filters + list_facets + new
      default limit), README tool table (7 tools now).
- [x] **Backlog additions** (`SOURCES-BACKLOG.md`, unprobed CANDIDATEs) — three
      Manhattan art museums (The Met, MoMA, The Whitney) under a shared "NYC art
      museums" note (curated adult-skewing → family-strand gate; the Met is a
      two-site `VENUE_NEIGHBORHOOD` case), and **The Skint** (theskint.com) — a
      citywide WordPress RSS blog flagged with the two probe blockers that decide
      buildability: digest-vs-per-event item granularity and low kid-yield.

### Session: Neighborhood coding + geocoding (branch `claude/neighborhood-event-coding-wkb2dh`)

Implemented Phase 3 A1's neighborhood + geocoding half (feat-006 + feat-009
partial). Neighborhoods are now populated by a **second nightly pass**, not by
sources.

- [x] **`enrich.py` second pass** — runs at the tail of `ingest.main` (guarded;
      `ENRICH=0` skips). Resolution ladder, first hit wins, only for rows with
      `neighborhood IS NULL`:
      1. fixed-venue source constant (`SOURCE_NEIGHBORHOOD`)
      2. enumerable multi-site (`VENUE_NEIGHBORHOOD`, NY Transit's 2 sites)
      3. open-data park table (`park_neighborhoods.json`, ~91% of permit rows)
      4. reverse-geocode existing lat/lng → NTA
      5. forward-geocode `"venue, city, NY"` → lat/lng (backfilled) + NTA
- [x] **`geocode.py`** — US Census geocoder client (forward + reverse, no key).
      Tract GEOID → NTA via the committed `tract_to_nta.json` crosswalk.
- [x] **`geocode_cache` table** in `events.db` (no TTL; negatives cached too) —
      a venue is geocoded at most once ever, so the nightly re-run is cheap even
      though the upsert nulls `neighborhood` each ingest.
- [x] **`sources/_neighborhoods.py`** — the static tables + `static_neighborhood()`
      + `nta_for_tract()`. Sibling to `_filters.py`.
- [x] **Open-data tables + build scripts** — `build_tract_nta.py` (Socrata
      `hm78-6dwm`, 2327 tracts), `build_park_neighborhoods.py` (Parks Properties
      `enfh-gkve` + Census batch/centroid, 1909 park keys), and
      `build_library_neighborhoods.py` (NYC FacDB `ji82-xba5` + Census, 221
      keys). Shared Census primitives in `scripts/_census.py`. JSON committed
      under `src/nyc_events/data/`.
- [x] **Library table** — `library_neighborhoods.json`, keyed
      `"<borough>|<library-core>"` (`library_core()` strips generic
      library/branch tokens). Codes **all 15** BPL feed branches; gated on a
      `library` token so a park can't borrow a library entry; borough-keyed to
      future-proof QPL/NYPL. `static_neighborhood()` now takes `borough`.
- [x] **Egress documented** — ingest already needs outbound HTTPS (all sources
      fetch external hosts); enrich adds `geocoding.geo.census.gov`. No egress
      allowlist in compose. Debt noted: if egress is ever hardened, add the host.
- [x] **BAM** added to `SOURCES-BACKLOG.md` as a CANDIDATE to probe (BAMkids;
      likely Tessitura — verify).
- [x] **Library systems backlog** — added Queens Public Library, NYPL, Bronx,
      and Staten Island as CANDIDATE items, with a system-map note: NYC has 3
      systems (BPL built, QPL, NYPL=Manhattan+Bronx+SI), so Bronx/SI are NYPL
      borough slices. Neighborhood coding already covers all of them (the
      library table is NYC-wide + borough-keyed) — a future source just needs to
      set each event's branch borough.
- [x] **Server** — `neighborhood` added to `_event_summary`; `search_events`
      gains a `neighborhood` filter (case-insensitive substring) wired through
      `db.search`.
- [x] **Tests** — `test_neighborhoods.py`, `test_enrich.py` (injected geocoders,
      no network), `test_event_projection.py`, plus geocode-cache + neighborhood-
      filter cases in `test_db.py`. **436 passed, ruff clean.**
- [x] **Docs** — CLAUDE.md (new "Neighborhood coding" section + Commands/Layout/
      Test/roadmap), PHASE-3-PLAN.md (A1 marked done, geocoder/cache decisions
      settled), README (ingest cron + permit-limits + tools), progress.md.

### Session: Filter-review pass (PR #21, open)

Implemented every decision from `FILTER-REVIEW.md` — the cross-source
kid-relevance filters had drifted between six hand-maintained copies.

- [x] **obs. 1 — drop alcohol-tasting terms** everywhere: `cocktail`,
      `whiskey`/`whisky`, `sake`, `brewery`, `distillery`, `wine tasting`,
      `beer tasting`, `happy hour`. Alcohol at a venue isn't itself an
      adult-only signal; these dropped legit family events. Industry City now
      keeps the gourmet-tour + sake-class rows.
- [x] **obs. 1 leftover + obs. 2 — shared `src/nyc_events/sources/_filters.py`:**
      `normalize()` (collapse hyphens/whitespace so one spelling matches all
      variants), `contains_any()`, and the canonical sets `ADULT_BLOCKLIST`
      (title or body), `ADULT_TITLE_BLOCKLIST` (drag show/brunch — title only),
      `MEMBERS_ONLY`. The six editorial sources import these; venue extras stay
      local (`gala`/`qc ny` for Governors Island, `Nightlife`/`late night` for
      Industry City).
- [x] **obs. 3 — Green-Wood dead blocklist removed.** The soft
      `_BLOCKLIST_KEYWORDS` was unreachable (allowlist short-circuits first,
      default is a conservative drop). `adults only` moved into the shared
      hard-exclude so it actually overrides the allowlist.
- [x] **obs. 4 — word-boundary tag matching** across all keyword-tagging
      sources: `re.search(r"\b" + kw)`, so `art`≠start, `tree`≠street,
      `hill`≠Churchill, `walk`≠boardwalk, `sing`≠crossing, `moth`≠mother,
      `bus`≠business — prefixes (`puppet`→`puppets`) still match. Non-gating.
- [x] **`drag show`/`drag brunch` made title-only** (`ADULT_TITLE_BLOCKLIST`)
      so a family event whose body merely mentions an adjacent drag show is kept.
- [x] **Docs reconciled:** `FILTER-REVIEW.md` (all obs. marked resolved +
      per-source detail), `CLAUDE.md` (layout + hygiene section), and
      `SOURCES-BACKLOG.md` (tech-debt marked done).
- [x] **PR-workflow hook:** new PreToolUse hook
      `.claude/hooks/require-handoff-update.sh` (matcher
      `mcp__github__create_pull_request`) blocks PR creation unless
      `session-handoff.md` was updated for the branch (dirty / changed vs
      `origin/main` / in the latest commit). Fail-open on git errors. Documented
      in CLAUDE.md's new "PR workflow" section.

### Session: Issues #4 / #5 / #6 (merged in PR #19)

- [x] **Issue #4 — FTS5 VACUUM footgun (doc fix):** CLAUDE.md "DB migrations"
      section now warns never to run `VACUUM` on `events.db` without
      immediately rebuilding the FTS5 index. The `events` table has a TEXT
      primary key, so SQLite may renumber implicit rowids on VACUUM and silently
      desynchronize the external-content FTS5 index.
- [x] **Issue #5 — Split consent password from master bearer:** Added optional
      `MCP_CONSENT_PASSWORD` env var. `/authorize` POST checks it first, falling
      back to `MCP_AUTH_TOKEN`. With it set, the browser consent form never
      touches the master bearer; the two credentials rotate independently.
      `.env.example` and CLAUDE.md updated.
- [x] **Issue #6 — Hygiene grab-bag:** OAuth DB churn reduced (5-min in-memory
      token cache in `BearerAuthMiddleware`); rate-limiter buckets now evict
      when empty; tool args clamped (`limit ≤ 50`, `days_ahead ≤ 365`);
      `UTC = UTC` no-ops removed; `import json` moved to module level;
      Green-Wood `_strip_html` now uses `html.unescape()`.

## Current state

Suite: **504 passed**, ruff: **clean** (after the `nycgovparks_events`
build — new source + 23 parser tests in the working tree, uncommitted).
Older per-branch notes below:

Suite: **461 passed**, ruff: **clean**. Security issues **#33/#34/#36**
(High + both Mediums from the 2026-07-05 review) implemented on
`claude/security-issues-review-q0w33m`; security Lows **#35** (negative
`limit` clamp) and **#37** (container hardening opts) remain open, as do
architecture-review issues **#28–#29** (db.init/connect split, unused deps —
note #29's dep removals would also shrink the new `requirements.lock`).
Suite: **455 passed**, ruff: **clean**. Issues #25 (Tribe consolidation),
#26 (server split), and #27 (neighborhood persistence) implemented on
`claude/architecture-design-review-8r5735`. Architecture-review issues
**#28** (db.init/connect split) and **#29** (unused deps) now implemented on
`claude/github-issues-28-29-7ks73y`; **#30** is fully absorbed (items 1+2 with
#26, item 3 with #25).

**Deploy note for #27:** after this lands, corrections to the static
neighborhood tables need a one-off `docker exec … python -m nyc_events.enrich
--recode-all` to reach already-coded rows — the nightly wipe that used to
propagate them implicitly is gone (that wipe was the bug).

## Decisions made

- **Alcohol ≠ adult-only.** Alcohol-tasting terms removed from all blocklists;
  explicit `21+`/`adults only`/`no children`/`burlesque`/`drag` still gate.
- **Shared `_filters.py`, per-source extras stay local.** Hoist only the
  canonical adult sets + the normalizer; the inclusion *strategy* and
  venue-specific terms remain in each source.
- **`drag show`/`drag brunch` are title-only**; the core adult terms match
  title or body.
- **Green-Wood soft blocklist was dead code** — removed, adult terms promoted
  to the hard-exclude.
- **Handoff-before-PR is enforced by a hook**, not just convention — PR creation
  is blocked until `session-handoff.md` is updated for the branch.

## Blockers / risks

- **`guard-commit` hook** is active: any `git add` whose command text contains
  `.env`, `.venv`, or `data/*.db` is blocked. Run `pytest`/`ruff` (which use
  `.venv/bin/...`) in a *separate* Bash call from `git add`/`git commit`, or the
  hook trips on the literal `.venv` in the command string.
- **OAuth token cache** means a revoked token (row deleted from `oauth.db`)
  stays valid for up to 5 min in a running server.
- **Ingest egress (debt).** The enrich pass needs `geocoding.geo.census.gov`
  reachable at ingest time (ingest already needs outbound HTTPS for all
  sources). The repo has no egress allowlist; if the deployment ever adds one,
  add that host or neighborhood coding silently stops (guarded — no crash).

## Next session startup

1. Read `CLAUDE.md` (project guide — hard-won quirks, security baseline;
   `## Phase roadmap` is the canonical feature state).
2. Run `pytest tests/ -q` + `ruff check` — suite should be green.

## Recommended next steps

(Reset 2026-07-07 after the architectural review; the old list was stale —
`near_me` was declined, PR #21 merged long ago.)

(Fixed this session: #39, #40/#62, #59, #35, #61, and #65 — see the top two entries.)

1. **#41 (P0) — wrong-borough park→NTA.** Now the top remaining P0. Needs
   `park_neighborhoods.json` rebuilt borough-keyed (Census network) + a
   borough guard in `static_neighborhood`'s park tier.
2. **One careful auth.py PR batching #63 + #64 + #69** (robustness 500s,
   GET /token log redaction, master-bearer rate-limit exemption) — it's the
   do-not-regress surface, so one reviewed PR with tests, not drive-bys.
3. **#44 — canonical tag vocabulary** (was re-filed as #66, now closed dup of
   #44) — prerequisite for any new source.
4. Then A2 indoor/outdoor → A3 weather → Workstream B (borough-gap order).
- **BAM** is queued in `SOURCES-BACKLOG.md` (CANDIDATE) — probe with
  `source-verifier` (likely Tessitura) before building.
