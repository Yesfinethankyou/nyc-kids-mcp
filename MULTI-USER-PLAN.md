# Multi-user plan (friends & family scale)

Plan for opening the server to a small circle of trusted users (~5–20 people:
friends and family). Written 2026-07-05; implementation not started. This
supersedes the "Out-of-scope: Multi-user" line in CLAUDE.md — update that
section when Phase A ships.

## Framing — what "multi-user" means here (and what it doesn't)

The catalog is **shared, read-only, public data** (NYC kids' events). Everyone
sees the same events; there is no per-user data, so this plan needs **no
tenancy, no data isolation, no per-user schemas, and no changes to
`events.db`**. All the work is on the auth layer plus a couple of
availability guardrails.

Mechanically, several people can already connect today: DCR accepts any
client, each claude.ai account gets its own `client_id` and its own access
token in `oauth_tokens`, and the bearer middleware validates any stored
token. The blocker is that everyone would type the same shared
`MCP_CONSENT_PASSWORD` on the consent page, which means:

- revoking one person requires rotating the credential for everyone, and
- tokens carry no attribution — you can't tell whose is whose.

Capacity is a non-issue: SQLite in WAL mode handles a dozen concurrent
readers trivially, MCP call volume per human is sparse, and the nightly
ingest's writes don't block reads.

## Phase A — per-person credentials (must ship before inviting anyone)

**Status: SHIPPED 2026-07-05** (users table + migration, consent-flow change,
`nyc_events.users` CLI, tests in `test_security_fixes.py`, CLAUDE.md/README
updates). Implementation notes live in CLAUDE.md § "OAuth model".

- [x] **Per-person invite codes replacing the shared consent password.**
      New `users` table in `oauth.db` (`user_id`, `name`, `passcode_hash` —
      salted hash, `created_at`, `revoked_at`). The consent form in `auth.py`
      stays one field; `authorize_post` looks up which non-revoked user's
      hash matches the presented code instead of comparing against one env
      var. Codes are **generated, high-entropy** (`secrets.token_urlsafe(16)`
      or longer), never human-chosen — the consent page is the
      online-guessing surface, and the per-IP limiter (5 req/10s on
      `authorize_post`) bounds attempts but shouldn't be the only defense.
- [x] **Token attribution.** Add `user_id` to `oauth_tokens` via an
      idempotent `_migrate_oauth` column-add (same pattern as `expires_at`).
      Stamped at `/token` time by threading the user through the auth code —
      the `AuthCode` dataclass in `oauth.py` gains a `user_id` field set by
      `authorize_post`.
- [x] **Admin CLI** — `python -m nyc_events.users`:
      - `add <name>` → creates the user, prints the invite code **once**
        (only the hash is stored);
      - `revoke <name>` → sets `revoked_at` AND deletes the user's
        `oauth_tokens` rows;
      - `list` → users + token counts + issue dates.
      This replaces the current hand-SQL revocation documented in CLAUDE.md.
      Note the 300-second in-process token cache in `auth.py` means a
      revocation takes effect within ~5 minutes — acceptable at this scale;
      don't add cache invalidation plumbing for it.
- [x] **`MCP_AUTH_TOKEN` stays operator-only.** It remains the direct-curl
      bearer and is never handed out. `MCP_CONSENT_PASSWORD` can be retired
      once the users table exists (or kept as the operator's own consent
      login during migration).
- [x] **Tests + docs.** New auth paths get tests in
      `test_security_fixes.py` — this is `auth.py`, the do-not-regress
      surface, so tests are not optional. Update CLAUDE.md (out-of-scope
      list, OAuth-model section, security baseline) and the README.

Phase A is the substantive work — roughly one focused session (table +
migration, consent-flow change, CLI, tests, docs).

## Phase B — hardening worth doing alongside

**Status: SHIPPED 2026-07-05** (same session as Phase A). Tokens are stored
as `sha256:<hex>` with a one-time in-place migration of legacy plaintext
rows; the MCP path has a 60 req/min per-token limit (master bearer
included); `auth.RedactAuthorizeQueryFilter` scrubs `/authorize` query
strings from uvicorn access logs.

- [x] **Hash access tokens at rest.** `oauth_tokens.access_token` is
      currently the plaintext token. Store `sha256(token)` instead and hash
      the presented bearer before lookup (`db.store_oauth_token` /
      `is_valid_oauth_token`). Cheap, and it means a leaked `oauth.db`
      backup doesn't leak live sessions — more relevant now that the DB
      holds other people's sessions. (The in-memory token cache can keep
      keying on the presented token; only the at-rest form changes.)
- [x] **Light per-token rate limit on the authenticated `POST /` path.**
      Today only the unauthenticated OAuth endpoints are limited; one
      person's runaway client shouldn't be able to starve the NAS. Generous
      (e.g. 60 req/min per token) — availability protection, not abuse
      defense.
- [x] **Revisit the auth-code-in-access-logs residual.** The code in
      `/authorize?...` query strings landing in uvicorn logs was an accepted
      residual single-user; with multiple users' codes flowing through,
      either drop query strings from access logs or re-confirm logs never
      leave the host.

Deliberately dropped from an earlier draft: shortening the token TTL below
the current 90 days (maintainer call, 2026-07-05 — a lost/stolen-device
window is not a concern for this user population).

## Phase C — availability guardrails (proportionate only)

- [ ] **Keep single-worker.** The in-process rate limiter, token cache, and
      pending auth codes force it (see CLAUDE.md security baseline), and
      uvicorn's async loop + millisecond SQLite reads handle this scale
      easily. Moving pending codes into `oauth.db` to unlock multi-worker is
      documented tech debt, not something to build now.
- [ ] **Back up `oauth.db`** alongside the NAS backup routine — losing it
      logs everyone out simultaneously, now a multi-person annoyance.
- [ ] **External uptime check** against the unauthenticated `/healthz`
      (e.g. Uptime Kuma on the NAS), since other people will notice outages
      before the operator does.
- The NAS + Tailscale Funnel remains the single point of failure. Accepted —
  set that expectation with users rather than engineering around it.

## Explicitly out of scope

- SSO / federated identity (stays out of scope per CLAUDE.md).
- Postgres / horizontal scaling / multiple workers.
- Per-user data, preferences, or saved searches.
- Admin UI — the `users` CLI is the admin surface.
