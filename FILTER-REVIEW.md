# Filter review

A single-page inventory of every source's kid-relevance **inclusion filter**
and **tag-inference** rules, compiled so the filters can be reviewed together
(tech-debt item from `SOURCES-BACKLOG.md`). This is a **review aid for a
human pass** — nothing here has been changed. The source code is authoritative;
this file was extracted from it and can drift, so re-introspect before acting:

```bash
# Dump the live filter constants from every source:
.venv/bin/python - <<'PY'
import importlib, pkgutil, re
import nyc_events.sources as S
HINT = re.compile(r"ALLOW|EXCLUDE|BLOCK|HARD|KEYWORD|KID|TAG|CATEG|RACE|AGE|SKIP|DROP", re.I)
for m in sorted(x.name for x in pkgutil.iter_modules(S.__path__)):
    if m in ("base","__init__","timeout_nykids"): continue
    mod = importlib.import_module(f"nyc_events.sources.{m}")
    hits = {n: v for n, v in vars(mod).items() if HINT.search(n) and not n.startswith("__")}
    if hits:
        print(f"\n== {m}"); [print(f"  {n} = {v!r}") for n, v in hits.items()]
PY
```

## How to read this

- **Inclusion gate** = what decides whether an event is kept or dropped. The
  order matters: most are short-circuit (first match wins).
- **Tag rules** = keyword → tag mappings used only to *label* kept events
  (they do **not** gate inclusion, except in `nyc_permitted_events`, where a
  row with zero tags is dropped). Tag bugs hurt output quality / filterability,
  not inclusion.
- All keyword matching is **case-insensitive substring** unless noted as a
  regex or an exact-equality check. Substring matching is the main source of
  false positives (e.g. `tree` ⊂ `s-tree-t`).

## Sources by filtering strategy

| Source | Strategy | Inclusion gate (short-circuit order) | Fields scanned |
|---|---|---|---|
| `mommy_poppins` | **None** (curated kids aggregator) | keep all valid rows | — |
| `bk_childrens_museum` | **None** (a children's museum) | drop only title matching `\bclosed\b` | title |
| `brooklyn_army_terminal` | **Blocklist only** | drop if title starts with `live music concert` | title |
| `governors_island` | **Inclusive + blocklist** | hard-exclude (title+body) → title-exclude → race regex → else KEEP | title, body |
| `domino_park` | **Inclusive + blocklist** | hard-exclude (title+desc) → else KEEP | title, description |
| `ny_transit_museum` | **Category allowlist** | exclude-category → hard-exclude-title → require include-category | categories, title |
| `prospect_park` | **Category allowlist** | hard-exclude-title → require include-category | categories, title |
| `industry_city` | **Keyword allowlist** | exclude-category → hard-exclude → require allowlist | title, excerpt, description, categories |
| `greenwood_cemetery` | **Keyword allowlist** | hard-exclude-title → allowlist=KEEP → blocklist-title → else DROP | title, excerpt, description, categories |
| `bpl` | **Age band + keyword** | adult-age=DROP → kid-band/hint=KEEP → else require keyword hint | age field, title, tags |
| `nyc_permitted_events` | **Multi-layer (strictest)** | agency allowlist → event_type allowlist → title-blocklist regex → require ≥1 kid tag | agency, event_type, title |

---

## Per-source detail

### `mommy_poppins` — no inclusion filter
Curated kids' editorial source, so every parsed row is kept. `_KID_KEYWORDS`
is **tagging only**, now matched with a leading word boundary (obs. 4): bare
`art` matches `art`/`arts`/`artwork` but no longer `st-art`/`p-art`/`he-art`.
Residual: `show` (theater) still matches `showcase`/`shower` because both
*start* with `show` — a leading boundary can't separate them; negligible on a
curated feed.

### `bk_childrens_museum` — no inclusion filter
Everything at a children's museum is kid/family by default. Only gate:
`_SKIP_TITLE_RX = \bclosed\b` (drops closed-day rows). `_KID_KEYWORDS` is
tagging only, now leading-word-boundary matched (obs. 4): `art`/`make`/`draw`
no longer hit `smart`/`filmmaker`/`withdraw`.

### `brooklyn_army_terminal` — blocklist only
`_DROP_TITLE_PREFIX = "live music concert"` — drops the 21+ EDM nightclub
shows; keeps everything else. Tag rules: `_TITLE_TAG_RULES` (6).

### `governors_island` — inclusive + blocklist
- Shared `ADULT_BLOCKLIST` (title+body) + `ADULT_TITLE_BLOCKLIST` (title) +
  `MEMBERS_ONLY` (title) from `_filters.py`.
- `_TITLE_EXCLUDE` (title only, 8, local extras): `gala`, `beach club`,
  `after party`, `open bar`, `bike rental`, `citi bike`, `digital guide`,
  `qc ny`. (Hyphen variants like `after-party` now collapse via the normalizer;
  alcohol-tasting terms were removed earlier per maintainer review.)
- `_RACE_RX`: `\bnycruns\b|\bhalf marathon\b|\bmarathon\b|\b\d+\s?k\b`
- Tag rules `_TAG_RULES` (7) — matched with a leading word boundary (obs. 4),
  so `tree`/`hill`/`fort`/`walk` no longer hit `street`/`Churchill`/`comfort`/
  `boardwalk`.

### `domino_park` — inclusive + blocklist
- Shared `ADULT_BLOCKLIST` (title+desc) + `ADULT_TITLE_BLOCKLIST` (title) from
  `_filters.py`, no local extras (alcohol-tasting terms were removed earlier).
- `_CATEGORY_TAGS` (6) + `_KEYWORD_TAGS` (6), now leading-word-boundary matched
  (obs. 4). (Bare `art` was
  already removed here; `field`/`lawn` in `outdoors` are clean.)

### `ny_transit_museum` — category allowlist
- `_INCLUDE_CATEGORIES` (require one): `Family Programs`, `Nostalgia Rides`
- `_EXCLUDE_CATEGORIES`: `Members-Only Programs`, `Virtual Programs`
- Title defensive net: shared `ADULT_BLOCKLIST` + `ADULT_TITLE_BLOCKLIST` +
  `MEMBERS_ONLY` (all title only) from `_filters.py` (was a local 5-item list).
- `_CATEGORY_TAGS` (4) + `_TITLE_TAG_RULES` (3), now leading-word-boundary
  matched (obs. 4): `bus`⊄`business`, `story`⊄`history`.

### `prospect_park` — category allowlist
- `_INCLUDE_CATEGORIES` (8): `Audubon Center`, `Performing Arts`,
  `Lefferts Historic House`, `Film`, `Education`, `Kids`, `Carousel`,
  `Nature Programs`
- Title defensive net: shared `ADULT_BLOCKLIST` + `MEMBERS_ONLY` (title only)
  from `_filters.py` (was a local 5-item list; now the full shared set).
- `_CATEGORY_TAGS` (15) + `_TITLE_TAG_RULES` (4) — `_TITLE_TAG_RULES` now
  leading-word-boundary matched (obs. 4): `sing` no longer hits `crossing`.

### `industry_city` — keyword allowlist (required)
- `_EXCLUDE_CATEGORIES`: `Nightlife`
- Hard exclusions (haystack title+excerpt+desc): shared `ADULT_BLOCKLIST` +
  `_LOCAL_EXCLUDE = ("late night",)` from `_filters.py`. (Alcohol-tasting terms
  were removed earlier per maintainer review — now keeps the gourmet-tour +
  sake-class rows.)
- `_ALLOWLIST_KEYWORDS` (28, must match one): family/kids/all ages/workshop/
  craft/puppet/storytime/market/garden/… (see code)
- `_TAG_RULES` (6)

### `greenwood_cemetery` — keyword allowlist (required)
- Title hard-exclude: shared `ADULT_BLOCKLIST` + `MEMBERS_ONLY` from
  `_filters.py` (the adult terms were **promoted** from the old soft blocklist
  per obs. 3 so they override the allowlist; now sourced from the shared set).
- `_ALLOWLIST_KEYWORDS` (53, must match one): broad — family, tour, nature,
  music, holiday/seasonal terms, Día de los Muertos, etc.
- ~~`_BLOCKLIST_KEYWORDS`~~ **removed** (was dead code — see obs. 3). `gala`/
  `donor` now drop via the conservative default; the two adult-only terms moved
  to `_HARD_EXCLUDE_TITLE`.
- `_TAG_RULES` (9) — now matched with a leading word boundary (obs. 4), so
  `tree` no longer hits `s-tree-t`.

### `bpl` — age band + keyword fallback
- `_ADULT_AGES` (exact match → drop): `adult`, `older adults`, `adults`
- `_KID_AGE_BANDS` (exact → keep): `birth to five years`, `kids`,
  `teens & young adults`
- `_KID_AGE_HINTS` (substring → keep, 14): kid/teen/child/baby/toddler/
  storytime/family/all ages/…
- `_KID_KEYWORDS` (11) tagging. ⚠️ `game` ⊂ many words; `class` matches the
  `educational` rule broadly.

### `nyc_permitted_events` — multi-layer (strictest)
- `KEPT_AGENCY` (exact): `Parks Department`
- `KEPT_EVENT_TYPES` (allowlist set — see code)
- `TITLE_BLOCKLIST` (regex): religious terms, load-in/out, RC models, school
  identifiers (`PS \d+`, `I.S. \d+`, `MS \d+`…), `field day`, `school`,
  `private`, `reservation`, `office`, `outreach`
- `KID_KEYWORDS` (10) — **gating**: a row that matches no kid keyword has zero
  tags and is dropped (`if not tags: return None`).

---

## Cross-source observations (for your decision — not changed)

1. **Adult/alcohol blocklist drift.** Six sources carry adult blocklists but
   they're inconsistent. Coverage matrix of common terms:

   | term | gov_isl | domino | industry | greenwd | nytm | pp |
   |---|---|---|---|---|---|---|
   | `21+` | ✅ | ✅ | ✅ | — | ✅ | ✅ |
   | `18+` | ✅ | ✅ | ✅ | — | — | — |
   | `adults only` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
   | `burlesque` | ✅ | ✅ | ✅ | — | — | — |
   | `drag show`/`drag brunch` | ✅ | ✅ | ✅ | — | — | — |
   | `members only` | ✅(title) | — | — | ✅ | ✅ | ✅ |

   **RESOLVED (maintainer review):** the alcohol-tasting terms — `cocktail`,
   `whiskey`/`whisky`, `sake`, `brewery`, `distillery`, `wine tasting`,
   `beer tasting`, `happy hour` — were removed from every source's blocklist.
   Rationale: alcohol at a venue is not by itself an adult-only signal, and
   these terms dropped legitimate family events (food-and-drink markets, a
   family sake/brewery tour). Explicit `21+` / `adults only` / `no children` /
   `burlesque` / `drag show` still gate genuinely adult content.

   **Hoisting also done:** the shared adult sets now live in
   `sources/_filters.py`: `ADULT_BLOCKLIST` (`21+`, `18+`, `adults only`,
   `adult only`, `no children`, `burlesque` — strong enough to drop on a match
   in title *or* body), `ADULT_TITLE_BLOCKLIST` (`drag show`, `drag brunch` —
   **title only**, so a family event whose body merely mentions an adjacent drag
   show isn't dropped), and `MEMBERS_ONLY`. Imported by Governors Island, Domino
   Park, Industry City, NY Transit, Prospect Park, and Green-Wood. Per-source
   extras stay local (`gala`/`qc ny`/etc. for Governors Island, the `Nightlife`
   category and `late night` for Industry City). The body-vs-title scope of the
   shared sets is fixed; venue-specific extras keep their own scope.

2. **Spelling/variant inconsistency — RESOLVED.** `_filters.normalize()`
   lowercases and collapses runs of hyphens/whitespace, and `contains_any()`
   matches the shared lists through it, so a single spelling (`adults only`,
   `after party`, `members only`) now matches all variants (`adults-only`,
   `after-party`, `members-only`). The per-source lists hold one spelling each.

3. **`greenwood_cemetery` blocklist was dead code — RESOLVED.** Confirmed dead
   (allowlist short-circuits first, default drops), so `_BLOCKLIST_KEYWORDS` was
   removed. `adults only`/`for adults only` were promoted to
   `_HARD_EXCLUDE_TITLE` so they actually override the allowlist (consistent
   with the other sources); `gala`/`donor` drop via the conservative default.

4. **Bare-substring false positives in tag rules — RESOLVED.** Tag matching in
   `mommy_poppins`, `bk_childrens`, `governors_island`, `greenwood_cemetery`,
   and `prospect_park` now uses a **leading word boundary** (`re.search(r"\b"
   + kw)`), so `art`/`tree`/`hill`/`fort`/`walk`/`sing` no longer match
   `start`/`street`/`Churchill`/`comfort`/`boardwalk`/`crossing`, while prefix
   matches (`tree`→`trees`, `puppet`→`puppets`) still work. Residual: `show`
   (mommy_poppins) still matches `showcase`/`shower` — both *start* with
   `show`, so a leading boundary can't separate them (negligible on a curated
   feed). Also fixed in `domino_park` (`moth`⊄mother, `dj`⊄adjust) and
   `ny_transit` (`bus`⊄business, `story`⊄history) with the same leading-boundary
   match.

5. **No-filter sources skew on trust.** `mommy_poppins` and `bk_childrens`
   keep everything by design. If either upstream ever broadens beyond kids,
   there's no safety net — fine today, worth a note.

6. **Allowlist vs inclusive split is intentional and venue-driven** — permit
   registry & generic-calendar venues (Industry City, Green-Wood) require an
   allowlist; family-destination parks (Governors Island, Domino Park) are
   inclusive + blocklist; category-tagged venues (Transit Museum, Prospect
   Park) gate on category. Keep this; the review is about *consistency within
   each strategy*, not collapsing them into one.

## Suggested review checklist

- [x] Drop alcohol-tasting terms from every blocklist (obs. 1, done). Still
      open: hoist the shared adult blocklist into one constant vs. per-source.
- [x] Normalize hyphen/space variants (obs. 2, done) — `_filters.normalize()` +
      `contains_any()`; the shared adult blocklist was also hoisted into
      `sources/_filters.py` (obs. 1 leftover).
- [x] Confirm + remove/repair Green-Wood's dead blocklist (obs. 3, done).
- [x] Word-boundary the short tag keywords (obs. 4, done for all flagged
      sources plus `domino_park` and `ny_transit`).
- [x] Live dry-run per touched source — kept counts unchanged after the
      `_filters.py` refactor (greenwood 87, governors 71, industry 21, domino
      105, prospect 303, nytm 12; verified 2026-06-21).
- [x] After any change: `pytest tests/ -q` + `ruff check` (407 passed, clean).
