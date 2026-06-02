# Future source backlog (candidates — UNVERIFIED)

Research notes for six NYC venues proposed for future Phase 2 integration.
**Nothing here is confirmed.** Every "format" below is a best-evidence
hypothesis from web search, not direct inspection — see "Why unverified".

Before any of these becomes a real source, run the verification probe for it
**from outside the sandbox** (your laptop or the NAS) and update its status
from `CANDIDATE` to `CONFIRMED` (or `REJECTED`) with what you actually saw.
Only then pick the best data source and run the `source-adder` recipe.

## Status legend

- `CANDIDATE` — plausible source found, format guessed, not yet fetched.
- `CONFIRMED` — probed from outside the sandbox; format + endpoint verified.
- `REJECTED` — probed, no usable structured source.

## Why unverified (cross-cutting blockers)

1. **Anti-bot 403.** Every consumer site here (Industry City, Domino,
   Green-Wood, Governors Island, Coney Island, MiLB) returned HTTP 403 to
   plain fetchers — the same wall Mommy Poppins needed `curl_cffi`
   (`impersonate="chrome"`) to clear. Expect to need `curl_cffi` for all of
   them. The one exception is the MLB Stats **API** (below), which is a JSON
   API, not a protected web page.
2. **Sandbox egress allowlist.** Cloud/web Claude sessions can't reach these
   domains at all (`"Host not in allowlist"`), so **fixtures must be captured
   outside the sandbox** — exactly like the BPL blocker. This is *the* reason
   we verify externally first.

## How to verify (run OUTSIDE the sandbox)

Generic platform probe — prints HTTP status and the tells that tell you the
CMS and whether a structured feed exists:

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
    "https://www.coneyisland.com/event",
    "https://www.green-wood.com/calendar/",
    "https://www.dominopark.com/events",
    "https://govisland.com/calendar",
]:
    probe(u)
```

Interpreting the tells:
- `tribe-events` / `/wp-json` → WordPress **The Events Calendar**. Try the
  REST API `…/wp-json/tribe/events/v1/events?per_page=10` and the iCal feed
  `…/events/?ical=1`. **Best case** — no HTML parsing needed.
- `squarespace` / `static1.squarespace` → Squarespace. Append `?format=json`
  to the events collection URL for raw JSON; also expect `application/ld+json`
  per event. (Same JSON-LD path Mommy Poppins already uses.)
- `application/ld+json` only → scrape, parse the JSON-LD `Event` objects.
- `eventbrite` → events are on Eventbrite; their public event-search API is
  deprecated, so prefer the JSON-LD on the organizer page over an API key.

---

## 1. Brooklyn Cyclones — **strongest candidate**

- **Status:** CANDIDATE (high confidence)
- **Source:** MLB Stats API — `https://statsapi.mlb.com/api/v1/schedule`
- **Format:** public JSON API, **no key, no anti-bot** (it's an API, not a page)
- **Why it stands out:** the only non-scraper here. No `curl_cffi`, no HTML,
  least fragile source in the whole project. Also covers the "Coney Island"
  geography via the home venue (Maimonides Park, Coney Island).
- **Verify (plain curl is fine — no impersonation needed):**
  ```bash
  # 1. find the Cyclones teamId (High-A South Atlantic League = sportId 13)
  curl -s "https://statsapi.mlb.com/api/v1/teams?sportId=13&season=2026" \
    | python3 -c "import sys,json;[print(t['id'],t['name']) for t in json.load(sys.stdin)['teams'] if 'Cyclones' in t['name']]"
  # 2. pull the home schedule with that teamId
  curl -s "https://statsapi.mlb.com/api/v1/schedule?sportId=13&teamId=<ID>&startDate=2026-04-01&endDate=2026-09-30" \
    | python3 -m json.tool | head -60
  ```
- **Confirm:** game date/time, home vs away, opponent, `venue.name`. Decide
  whether to ingest away games (probably home-only).
- **Build notes:** each game is its own occurrence; `external_id = gamePk`
  (stable per-game id from the API). No description/age fields — these are
  ballgames; tag `sports`/`family`, set a sensible `low_confidence=false`
  only if we synthesize a decent title/description.
- **ToS:** statsapi is public-but-unofficial; widely used. Be polite, cache.

## 2. Industry City

- **Status:** CANDIDATE (medium)
- **Source:** `https://industrycity.com/events/` ("Events Archive" page title)
- **Hypothesis:** WordPress + The Events Calendar (tribe).
- **Verify (outside sandbox, with curl_cffi):**
  ```bash
  # run the generic probe above on /events/, then if tribe/wp-json shows up:
  #   REST:  https://industrycity.com/wp-json/tribe/events/v1/events?per_page=5
  #   iCal:  https://industrycity.com/events/?ical=1
  ```
- **Confirm:** whether REST + iCal are enabled (some sites disable wp-json).
  If yes → cleanest scraper-tier source (structured JSON + iCal).

## 3. Coney Island → **Coney Island USA** (not Luna Park)

- **Status:** CANDIDATE (medium)
- **Source:** `https://www.coneyisland.com/event` (listing) + `/event/<slug>`
- **Hypothesis:** Squarespace → JSON-LD per event + `?format=json`.
- **Important:** "Coney Island" is ambiguous. **Luna Park**
  (lunaparknyc.com) publishes *park hours / seasons*, not discrete events —
  **do not pursue it** as an events source. The eventful entity is Coney
  Island USA (museum, sideshow, Mermaid Parade, film festival).
- **Verify:** probe `/event`; try `https://www.coneyisland.com/event?format=json`.
- **Confirm:** Squarespace JSON shape OR JSON-LD `Event` blocks on detail pages.

## 4. Green-Wood Cemetery

- **Status:** CANDIDATE (medium)
- **Sources:** `https://www.green-wood.com/calendar/` **and** Eventbrite
  organizer `1373401985` (`https://www.eventbrite.com/o/1373401985`)
- **Hypothesis:** WP calendar + ticketing on Eventbrite; JSON-LD on both.
- **Verify:** probe the WP calendar; on the Eventbrite org page grep for
  `"@type":"Event"` JSON-LD. (Eventbrite's public event-search API is
  deprecated — don't plan around an API key.)
- **Confirm:** which surface lists the *full* event set (some tours are
  Eventbrite-only, some drop-ins WP-only). Genuinely kid-relevant: family
  drop-ins at the Green-House, nature tours.

## 5. Domino Park

- **Status:** CANDIDATE (low — platform unknown)
- **Source:** `https://www.dominopark.com/events` (Two Trees property)
- **Hypothesis:** unknown CMS behind the 403; scrape, JSON-LD TBD.
- **Verify:** run the generic probe; check `/wp-json/` and `?format=json`.
- **Confirm:** platform + whether events are server-rendered or JS-loaded.
  Strong family programming (fitness, family days, SKATE) if structured.

## 6. Governors Island

- **Status:** CANDIDATE (low — platform unknown)
- **Source:** `https://govisland.com/calendar`
- **Hypothesis:** unknown CMS; scrape, JSON-LD TBD. doNYC mirror exists as a
  fallback aggregator (`donyc.com/venues/governors-island`).
- **Verify:** run the generic probe; check `/wp-json/`, `?format=json`,
  and whether the calendar is JS-rendered (if so, look for the XHR/API it
  calls in browser devtools → that's the real source).
- **Confirm:** platform + structured feed. Heavy family/public programming,
  clearly in scope.

---

## Provisional priority (revisit after verification)

1. **Brooklyn Cyclones** — clean JSON API, do first.
2. **Industry City** — likely iCal/REST, near-trivial if TEC confirmed.
3. **Coney Island USA** — Squarespace JSON-LD, reuses Mommy Poppins' path.
4. **Green-Wood** → **Domino Park** → **Governors Island** — pure scrapes,
   lowest confidence until the live HTML is inspected.

This order is a guess weighted by source quality, **not a commitment** —
re-rank once the probes above turn `CANDIDATE` into `CONFIRMED`.
