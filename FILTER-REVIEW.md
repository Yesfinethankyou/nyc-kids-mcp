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
is **tagging only**. Note bare `art` in the `arts & crafts` rule (matches
`st-art`, `p-art`, `he-art`) and `show` in `theater` (matches `showcase`,
`shower`).

### `bk_childrens_museum` — no inclusion filter
Everything at a children's museum is kid/family by default. Only gate:
`_SKIP_TITLE_RX = \bclosed\b` (drops closed-day rows). `_KID_KEYWORDS` is
tagging only; also has bare `art` and `make` / `draw`.

### `brooklyn_army_terminal` — blocklist only
`_DROP_TITLE_PREFIX = "live music concert"` — drops the 21+ EDM nightclub
shows; keeps everything else. Tag rules: `_TITLE_TAG_RULES` (6).

### `governors_island` — inclusive + blocklist
- `_HARD_EXCLUDE` (title+body, 7): `21+`, `18+`, `adults only`, `adults-only`,
  `adult-only`, `no children`, `burlesque`
- `_TITLE_EXCLUDE` (title only, 13): `gala`, `beach club`, `after party`,
  `after-party`, `drag show`, `drag brunch`, `open bar`, `members only`,
  `members-only`, `bike rental`, `citi bike`, `digital guide`, `qc ny`
  (alcohol-tasting terms `cocktail`/`wine tasting`/`beer tasting`/`happy hour`
  removed per maintainer review — alcohol alone isn't an adult-only signal)
- `_RACE_RX`: `\bnycruns\b|\bhalf marathon\b|\bmarathon\b|\b\d+\s?k\b`
- Tag rules `_TAG_RULES` (7). ⚠️ substring risks: `tree` ⊂ `street`,
  `hill` ⊂ `Churchill`, `fort` ⊂ `comfort`/`effort`, `walk` ⊂ `boardwalk`.

### `domino_park` — inclusive + blocklist
- `_HARD_EXCLUDE` (title+desc, 9): `21+`, `18+`, `adults only`,
  `adults-only`, `adult-only`, `no children`, `burlesque`, `drag show`,
  `drag brunch` (alcohol-tasting terms `wine tasting`/`beer tasting`/
  `happy hour` removed per maintainer review)
- `_CATEGORY_TAGS` (6) + `_KEYWORD_TAGS` (6) for tagging. (Bare `art` was
  already removed here; `field`/`lawn` in `outdoors` are clean.)

### `ny_transit_museum` — category allowlist
- `_INCLUDE_CATEGORIES` (require one): `Family Programs`, `Nostalgia Rides`
- `_EXCLUDE_CATEGORIES`: `Members-Only Programs`, `Virtual Programs`
- `_HARD_EXCLUDE_TITLE` (5): `21+`, `adults only`, `adults-only`,
  `members only`, `members-only`
- `_CATEGORY_TAGS` (4) + `_TITLE_TAG_RULES` (3)

### `prospect_park` — category allowlist
- `_INCLUDE_CATEGORIES` (8): `Audubon Center`, `Performing Arts`,
  `Lefferts Historic House`, `Film`, `Education`, `Kids`, `Carousel`,
  `Nature Programs`
- `_HARD_EXCLUDE_TITLE` (5): `21+`, `adults only`, `adults-only`,
  `members only`, `members-only`
- `_CATEGORY_TAGS` (15) + `_TITLE_TAG_RULES` (4)

### `industry_city` — keyword allowlist (required)
- `_EXCLUDE_CATEGORIES`: `Nightlife`
- `_HARD_EXCLUDE` (10): `21+`, `adults only`, `adults-only`, `18+`,
  `burlesque`, `drag show`, `drag brunch`, `late night`, `late-night`,
  `no children` (alcohol-tasting terms `cocktail`/`whiskey`/`whisky`/`sake`/
  `brewery`/`distillery`/`wine tasting`/`beer tasting`/`happy hour` removed per
  maintainer review — now keeps the gourmet-tour + sake-class rows)
- `_ALLOWLIST_KEYWORDS` (28, must match one): family/kids/all ages/workshop/
  craft/puppet/storytime/market/garden/… (see code)
- `_TAG_RULES` (6)

### `greenwood_cemetery` — keyword allowlist (required)
- `_HARD_EXCLUDE_TITLE` (2): `members only`, `members-only`
- `_ALLOWLIST_KEYWORDS` (53, must match one): broad — family, tour, nature,
  music, holiday/seasonal terms, Día de los Muertos, etc.
- `_BLOCKLIST_KEYWORDS` (4): `gala`, `donor`, `for adults only`,
  `adults only` (`cocktail` removed per maintainer review) — ⚠️ **likely dead
  code**: the function returns `True` on any
  allowlist hit and otherwise falls through to a conservative `return False`,
  so a blocklist term is only reachable on a row that has **no** allowlist hit,
  which is already dropped. Worth confirming/ removing or reordering.
- `_TAG_RULES` (9). ⚠️ `sing` ⊂ `crossing`, `bird` is fine, `tree` clean here.

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
   `burlesque` / `drag show` still gate genuinely adult content. The remaining
   open question is whether to hoist the **shared** adult blocklist (`21+`,
   `adults only`, `burlesque`, `drag …`) into a `sources/_filters.py` constant
   instead of per-source copies; per-source extras (e.g. `qc ny` for Governors
   Island) stay local.

2. **Spelling/variant inconsistency.** `adults only` vs `adults-only` vs
   `adult-only`; `members only` vs `members-only`; `late night` vs
   `late-night`. A shared normalizer (strip/lower/collapse hyphens before
   matching) would let each list hold one spelling.

3. **`greenwood_cemetery` blocklist is probably dead code** (see above).

4. **Bare-substring false positives in tag rules** (output quality, not
   inclusion): `art` ⊂ start/part/heart (`mommy_poppins`, `bk_childrens`);
   `tree`/`hill`/`fort`/`walk` (`governors_island`); `sing` ⊂ crossing
   (`greenwood`). Consider word-boundary regexes for short keywords.

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
- [ ] Normalize hyphen/space variants (obs. 2).
- [ ] Confirm + remove/repair Green-Wood's dead blocklist (obs. 3).
- [ ] Word-boundary the short tag keywords (obs. 4).
- [ ] Spot-check each allowlist source against a fresh live fetch for false
      negatives (legit kid events dropped).
- [ ] After any change: `pytest tests/ -q` + `ruff check`, and a live dry-run
      per touched source (`list(SomeSource().fetch())`) to compare kept counts.
