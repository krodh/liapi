# Architecture

How the service is put together, and why each piece is the way it is.

For *how the API was discovered*, see
[reverse-engineering.md](./reverse-engineering.md). For *how to obtain a
credential*, see [auth.md](./auth.md).

## The shape of it

```
                  ┌──────────────┐
   HTTP request → │  main.py     │  routes, validation, error mapping
                  └──────┬───────┘
                         │
              ┌──────────▼──────────┐
              │  cache.py           │  hit? → return, no upstream call
              └──────────┬──────────┘
                         │ miss
              ┌──────────▼──────────┐      ┌───────────┐
              │  session.py         │◄─────│  auth.py  │  auth.json
              │  the Voyager client │      └───────────┘
              └──────────┬──────────┘
                         │ raw JSON
              ┌──────────▼──────────┐
              │  models.py          │  normalized payload → Profile
              └──────────┬──────────┘
                         │
                    HTTP response
```

Each module has one job and does not reach around its neighbours:

| Module | Responsibility |
| --- | --- |
| `config.py` | settings, from the environment or `.env` |
| `auth.py` | the `auth.json` credential: schema, cookie tiers, load/save |
| `capture.py` | browser-assisted capture — **setup time only**, never imported by the API |
| `session.py` | the Voyager HTTP client: headers, pacing, failure detection |
| `models.py` | the response schema and the parsers that produce it |
| `cache.py` | on-disk profile cache |
| `main.py` | FastAPI routes |
| `cli.py` | `capture`, `status`, `serve` |

`session.py` knows nothing about profiles; `models.py` knows nothing about
HTTP. That separation is what let the profile endpoint be replaced (when
LinkedIn retired the old one) without touching the client, and the parser be
rewritten without touching either.

## Request flow

`GET /profile/{identifier}` does this:

1. **Resolve the identifier.** A full URL, a percent-encoded URL, or a bare
   vanity slug all reduce to a public id. Anything else is a `422`.
2. **Check the cache.** A fresh entry is returned immediately — no LinkedIn
   call at all.
3. **Load the credential** from `auth.json`. Missing or malformed is a `503`,
   not a crash: the fix is operational, so the error says so.
4. **Warm up.** Fetch `/feed/` as a page load and discard it. A browser always
   renders a page before its JavaScript issues XHRs; leading with a bare API
   call is an automation tell.
5. **Fetch the profile** — one request, which returns the profile plus its
   positions, educations, companies, schools, geo and industry.
6. **Fetch the detail sections** (skills, certifications, languages) — one
   request each, best-effort. A section that fails leaves its field empty
   rather than failing the whole response. Skipped entirely with
   `?sections=false`.
7. **Parse** the normalized payload into the `Profile` model.
8. **Cache** the result and return it.

## The client

The session token is bound to the client that minted it, so the client's job is
to be *consistent with the browser the credential came from* — not to imitate
some generic browser.

- **TLS fingerprint.** `curl_cffi` impersonates the captured browser, so the
  handshake matches the user agent we send. Plain `requests` has an obviously
  non-browser fingerprint regardless of headers.
- **Headers from the capture.** User agent, locale and the `x-li-track`
  telemetry blob (LinkedIn client version, timezone, screen size) come from
  `auth.json`, not from constants. Hardcoding them would mean claiming a
  different machine than the one holding the cookies.
- **Correct request kind.** API calls send XHR fetch-metadata
  (`sec-fetch-mode: cors`); the warmup page load sends document metadata. We
  previously sent document metadata on API calls — announcing a page navigation
  while asking for JSON, which no browser does.
- **Pacing.** A jittered 2–5s pause between calls, and a shorter 0.4–1.2s burst
  between the section calls that belong to one logical page view. Measurement
  showed pacing does *not* extend a session, but it is cheap and keeps traffic
  ordinary.
- **Failure detection.** `li_at=delete me` in a response means LinkedIn deleted
  the session; any 3xx means it was refused. Both raise `SessionKilled`, which
  the API maps to `503`. Redirects are never followed — an unauthenticated
  Voyager call redirects to itself, forever.

## Parsing

Voyager returns Rest.li's normalized form:

```json
{ "data": { … }, "included": [ { "$type": …, "entityUrn": … }, … ] }
```

Entities live flat in `included`, keyed by URN, and reference each other by URN
string on `*`-prefixed keys. Parsing indexes `included` and resolves through it.

Two different shapes have to be handled, because LinkedIn serves the profile and
its detail sections differently:

**The profile** is entities. `_Index` looks them up by `$type` and by URN.
Resolution is sometimes indirect — location is `geoLocation.*geo` → a `Geo`,
then its `*country` → another `Geo`. A position is current only when its date
range has no `end`.

**The sections** are *rendered UI components*, not entities. Readable text sits
at `entityComponent.titleV2.text.text`, and entries nest at no fixed depth: a
short list is inline, a longer one is wrapped in a `pagedListComponent`
delivered through `included`, and skills arrive inside a `tabComponent`. The
parser walks the whole component tree collecting every `entityComponent`, which
covers all three without encoding a layout LinkedIn changes freely.

Which field holds the detail also varies: a certification names its issuer in
`subtitle`, a language its proficiency in `caption`.

## Caching

**On disk, one JSON file per profile**, at `cache/profiles/<username>.json`:

```json
{
  "public_id": "someone",
  "cached_at": "2026-08-30T09:05:18+00:00",
  "sections": true,
  "profile": { … }
}
```

Why disk rather than memory: a fetch costs several deliberately paced upstream
calls (~9 seconds) and spends part of a session's goodwill. An in-memory cache
loses all of that on every restart or redeploy — precisely when a hosted service
restarts most. On disk, the work survives.

Three details worth knowing:

- **`cached_at` drives expiry**, not file mtime, which lies as soon as a file is
  copied or restored.
- **`sections` records how complete the entry is.** An entry fetched with
  `?sections=false` cannot answer a later request that wants them; the reverse
  is fine, since a fuller record still answers a lighter request.
- **Writes are atomic** — to a temporary file, then renamed — so a crash
  mid-write cannot leave a half-written entry that later reads treat as corrupt.

Filenames are derived from user input, so anything outside `[A-Za-z0-9._-]` is
replaced; a public id cannot escape the cache directory.

`?refresh=true` bypasses a hit and rewrites the entry.

## Error mapping

Upstream messiness is translated into a small, honest set of statuses:

| Status | Meaning | Cause |
| --- | --- | --- |
| `422` | not a valid profile URL or public id | the identifier did not parse |
| `502` | upstream error | LinkedIn returned something unexpected |
| `503` | no usable session | `auth.json` missing/malformed, or the session was invalidated |

`503` is deliberately distinct: it means *go re-capture the credential*, which
is an operational action, rather than leaving you debugging a parser.

## Configuration

All settings have working defaults; override via the environment or `.env`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `AUTH_FILE` | `auth.json` | the credential file |
| `CACHE_DIR` | `cache/profiles` | where cached profiles live |
| `CACHE_TTL` | `3600` | how long an entry stays fresh, in seconds |
| `MIN_DELAY` / `MAX_DELAY` | `2.0` / `5.0` | pacing bounds between upstream calls |
| `TIMEOUT` | `20.0` | upstream request timeout |

The two path settings resolve against the project root when left at their
defaults, so the CLI behaves identically from any working directory. An
explicitly set path resolves against the working directory, as a caller would
expect.

## What is deliberately not here

- **No browser at request time.** `capture.py` uses one, once, at setup;
  Playwright is an optional extra the API never imports.
- **No HTML parsing.** There is no HTML in the pipeline to parse.
- **No credential in the repository.** `auth.json` is gitignored and written
  `0600`.
- **No retries against a dead session.** A killed session is reported, not
  retried into a deeper flag.

## Testing

Tests are offline — no network, no credential. Parser tests run against a real
captured payload (`tests/fixtures/dash_profile.json`) so they lock in
behaviour against LinkedIn's actual response shape rather than an invented one.

```bash
pytest
```
