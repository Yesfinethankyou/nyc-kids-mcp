# Playwright / Playwright MCP assessment (2026-07-24)

Requested: assess whether integrating Playwright MCP would simplify this
project, unlock blocked sources, or provide other benefits. Includes live
probes run from this (web-sandbox) session — see "Empirical findings".

## Verdict

| Surface | Verdict |
|---|---|
| Nightly ingest runtime (NAS) | **No.** The 2026-07-07 STRUCK decision stands; Playwright MCP changes nothing in that ledger. |
| The served MCP server / dashboard | **No fit at all.** |
| Dev-session probing & fixture capture | **Yes — small, tooling-only adoption.** Real, immediate value; near-zero cost. This session validated the recipe. |

Playwright MCP does not simplify the existing project. Its value here is
narrow and specific: it (or plain Playwright scripting, which needs no MCP
server at all) turns several "blocked / headless-tier / needs-devtools"
backlog items into things a Claude Code session can investigate directly —
including from the web sandbox, which was previously documented as
impossible for WAF-walled hosts.

## What Playwright MCP actually is

Two different things get conflated under "integrate Playwright":

- **Playwright the library** — a runtime dependency that renders pages
  inside the ingest process. This is what PHASE-3-PLAN.md evaluated and
  **struck** (image weight ~300–450 MB, 300–700 MB RAM per render on the
  NAS, non-root/Dockerfile hardening friction, render flakiness).
- **Playwright MCP** (`microsoft/playwright-mcp`, `@playwright/mcp`) — an
  MCP server that gives an *AI agent* a browser at **interaction time**:
  `browser_navigate`, `browser_snapshot` (accessibility tree),
  `browser_click`/type/fill, `browser_evaluate` (run JS),
  `browser_network_requests` (list XHR/fetch traffic — a devtools Network
  tab), `browser_take_screenshot`. Installed per-project with
  `claude mcp add playwright npx @playwright/mcp@latest`; supports
  `--executable-path`, `--headless`, and arbitrary `browser.launchOptions`.

The critical consequence: **Playwright MCP is not a runtime fetch
mechanism.** The nightly ingest is a cron'd `docker exec` on the NAS with no
agent in the loop — an MCP browser server has no place in that pipeline, so
adopting it neither violates nor reopens the STRUCK decision. The two
decisions are orthogonal.

## Surface 1: the ingest runtime — keep STRUCK

Nothing found in this research shifts the 2026-07-07 cost/benefit:

- Every cost in the PHASE-3-PLAN.md ledger (image, RAM, hardening,
  flakiness, multi-arch builds) is unchanged in current Playwright.
- The benefit side is still thin. The rendering-required candidates remain
  nice-to-haves: Cyclones themed nights, Time Out NY Kids, BAM, Puppetworks,
  the Met, AMNH's full grid. None is a must-have, and the strongest of them
  (the two library systems) turned out **not** to need rendering at runtime —
  `curl_cffi` impersonation from the NAS already gets them.
- The repeated Phase-2/3 lesson holds: "no feed" verdicts keep dying to
  better *probing* (curl_cffi impersonation rescued Industry City, Governors
  Island, Domino Park; off-proxy probes rescued NYPL/QPL/CPF/Intrepid), not
  to rendering. The right investment is better probing, which is exactly
  what browser tooling at dev time provides.

If a future source genuinely needs runtime rendering, the contained design
in PHASE-3-PLAN.md (opt-in sync `_render()`, separate ingest image) is still
the recorded shape — reopen that decision then, on that source's evidence.

## Surface 2: the served MCP server — no fit

The product server is a hardened, single-worker, non-root events API behind
OAuth on a NAS. Browser tools bolted onto it would be new public attack
surface on the do-not-regress `auth.py` boundary, a resource hazard
(Chromium vs the NAS), and useless to its clients (claude.ai calls event
tools, not a browser). The dashboard is read-only by construction. Nothing
to do here.

## Surface 3: dev-session tooling — where the value is

The backlog is full of items whose *next step* is literally a browser task:

- **AMNH:** "spend a browser-devtools session hunting the XHR the grid
  makes" — **done this session, see below (answer: there is no grid XHR).**
- **The Met:** "unless a JSON endpoint turns up in browser devtools."
- **Macaroni Kid:** crack the `my.yodel.today/api/v3` events endpoint from
  the widget JS bundle.
- **Brooklyn Cyclones:** hunt the Contentful delivery token in the MiLB JS
  bundles; check the `mlb.tickets.com` rendered title for promo names.
- **BAM / MoMA / Whitney / Brooklyn Museum / NYSCI / Bronx River Alliance:**
  probe JS-driven calendars ("check with a real browser render before
  concluding either way").
- **Dashboard verification:** sessions already do ad-hoc headless-Chromium
  screenshots when available; this formalizes it.

The pattern that keeps the project's philosophy intact: **use the browser
once, at probe time, to find the underlying data channel — then build the
source on httpx/`curl_cffi` as always.** `browser_network_requests` is the
tool-shaped version of exactly that. A probe that finds *no* underlying
feed still means REJECT (Workstream B rule unchanged) — the browser
improves the quality of the verdict, it does not create a new source tier.

### Empirical findings (this session, web sandbox, 2026-07-24)

The web sandbox has Playwright's Chromium pre-installed
(`/opt/pw-browsers/chromium`). Naive launches fail
(`net::ERR_CONNECTION_RESET`, even on example.com) — the same
TLS-re-terminating-proxy failure documented for
`curl_cffi impersonate="chrome"`. **The fix is one flag:**

```python
browser = p.chromium.launch(
    headless=True,
    executable_path="/opt/pw-browsers/chromium",   # match the preinstalled build
    proxy={"server": os.environ["HTTPS_PROXY"]},
    args=["--ssl-version-max=tls1.2"],             # the MITM proxy resets TLS1.3 hellos
)
ctx = browser.new_context(ignore_https_errors=True)  # proxy CA
```

Results against the documented walls:

| Target | Wall | Result |
|---|---|---|
| `nypl.org/events/calendar` | Imperva Incapsula | **Falls.** HTTP 200, real calendar (954 KB), audience cells present. |
| `queenslibrary.org/calendar?...` | F5 BIG-IP | **Falls.** HTTP 200, real cards incl. the `arrJsonData_cal` blob the `qpl` parser reads. |
| `metmuseum.org/events` | Vercel Security Checkpoint | **Stands.** 429 challenge page, holds ≥20 s against headless Chromium. |
| `amnh.org/calendar` | (none) | Renders — and answers the backlog TODO: after full JS execution only ~6 event cards exist and **no calendar/data XHR fires** (traffic is 100% analytics). The featured cards *are* the calendar; there is no hidden grid endpoint on this page. |

Consequences:

- The "fixture capture must be off-proxy (laptop/NAS)" constraint in
  SOURCES-BACKLOG.md is now only **partially** true: NYPL and QPL fixtures
  can be recaptured from a web session via a browser render +
  `page.content()`. (Runtime is untouched — the NAS keeps `curl_cffi`.)
- If `nypl`/`qpl` ever break while only a web session is available,
  `source-fixer` has a working in-sandbox path.
- AMNH's "featured-only, ~8 rows" option is now the *only* option — the
  devtools question is closed. Ship it as a low-yield source or skip; no
  further probing needed on `/calendar`.
- The Met stays effectively unreachable even with a real browser from the
  sandbox; deprioritized status confirmed.

## Does anything get simpler? No.

The project's complexity lives in parse/filter/dedup semantics
(recurrence models, kid-relevance strategies, per-source quirks), which a
browser does not touch. The existing fetch layer is small, fast
(hundreds of requests/night), and NAS-cheap; replacing any of it with
rendering would be strictly slower, flakier, and heavier. The fixture-test
architecture (pure parsers against captured payloads) is already the right
shape and is unchanged by any of this.

## Recommendation

1. **Do not reopen** the runtime headless decision. (Nothing to do.)
2. **Adopt browser-assisted probing as dev tooling**, in either or both of
   two forms — both zero-impact on the shipped system:
   - **(a) The script recipe (available now, no config):** the launch
     snippet above, run via Bash in a session. This is what produced the
     findings in this document. Recorded in SOURCES-BACKLOG.md's
     cross-cutting notes so `source-verifier`/`source-fixer` sessions use it.
   - **(b) Optional — Playwright MCP for interactive probing:** add to
     `.mcp.json` for sessions that want tool-native navigation/snapshots
     instead of writing scripts:
     `npx @playwright/mcp@latest --headless --executable-path /opt/pw-browsers/chromium`
     (in the sandbox, launch args must include `--ssl-version-max=tls1.2`
     via config `browser.launchOptions`). Convenient, but (a) subsumes it
     functionally and is more reproducible; treat (b) as a
     convenience, not a dependency.
3. **Guardrails:** browser use is probe/fixture/verification only; every
   built source still fetches with httpx/`curl_cffi` at runtime; a probe
   that finds no underlying structured feed still means REJECT.

## Sources

- [PHASE-3-PLAN.md — headless STRUCK decision](PHASE-3-PLAN.md)
- [SOURCES-BACKLOG.md — cross-cutting notes + per-candidate probe history](SOURCES-BACKLOG.md)
- [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)
- [Playwright MCP docs](https://playwright.dev/docs/getting-started-mcp)
