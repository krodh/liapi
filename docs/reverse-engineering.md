# Reverse-engineering LinkedIn's Voyager API

How this service was built: what the target is, how it was investigated, what
was found, and — because it matters — which of our own conclusions turned out
to be wrong.

Everything below is measured. Where a claim rests on inference rather than
observation, it says so.

> What the service does with these findings today: [architecture.md](./architecture.md).

## Summary

| | |
| --- | --- |
| **Target** | `www.linkedin.com/voyager/api/…` — the private JSON API the LinkedIn SPA runs on |
| **Auth** | a logged-in session: `li_at` is the credential, `JSESSIONID` doubles as the CSRF token |
| **Profile fetch** | one call to `/identity/dash/profiles` — but only with the right `decorationId` |
| **Biggest gotcha** | the endpoint every public example uses (`profileView`) is retired: **HTTP 410** |
| **What decides durability** | cookie-jar completeness, not pacing. 2 cookies → dead in ~5 requests; 21 cookies → 125+ requests over hours |
| **How LinkedIn kills you** | it doesn't just refuse — it replies `set-cookie: li_at=delete me` |
| **What we got wrong** | twice blamed the environment — a flagged account, then network-degree gating — when both were our own bugs |

---

## 1. The target

LinkedIn's website is a single-page app. It renders little of substance
server-side; it fetches data from a private JSON API — "Voyager", the codename
of the 2016 SPA rewrite — and draws the result.

That API is the surface this service uses. Calling it directly means:

| | Browser automation | This service |
| --- | --- | --- |
| Request | load `linkedin.com/in/foo` | `GET /voyager/api/identity/dash/profiles?…` |
| Response | HTML + JS bundles | **JSON** |
| To extract data | render DOM, query selectors | `json.loads()` |
| Cost | ~300 MB Chrome, seconds | ~2 MB, one request |
| Breaks when | a CSS class changes | the API contract changes |

No browser runs at request time, no HTML is parsed, no JavaScript executes.

---

## 2. Method

Three modes, in increasing order of cost:

**Read the prior art.** The community libraries (`tomquirk/linkedin-api`, now
removed; the maintained fork `EseToni/open-linkedin-api`) establish the endpoint
shapes, the CSRF trick and `x-restli-protocol-version: 2.0.0`. Enough for a
first request; not enough to explain anything that followed.

**Instrument our own client.** Rather than guessing why sessions died, log every
response's `Set-Cookie` across sequential calls on one reused session, and
measure longevity under different traffic patterns.

**Use a real browser as a measuring instrument.** Drive Chromium, record every
Voyager request with full headers and query strings, snapshot the live cookie
jar — then **replay that jar through our own client** to isolate variables. The
browser is never part of the deliverable; it is how we learned what the
deliverable must send.

---

## 3. Findings

### 3.1 How LinkedIn kills a session

It does not merely reject the request — it deletes your credential:

```
HTTP 302
set-cookie: li_at=delete me; Max-Age=0; Expires=Thu, 01-Jan-1970 00:00:00 GMT
location: https://www.linkedin.com/voyager/api/me
```

Two consequences:

- **This is why capturing a cookie signs your own browser out.** The same
  `delete me` directive lands in the browser's jar. Tool and browser share one
  session; killing it kills both.
- **Naively persisting `Set-Cookie` would persist the deletion.** The client
  detects `li_at=delete me` and raises `SessionKilled` rather than carrying it
  forward or retrying into a deeper flag.

Note the redirect target: an unauthenticated Voyager call 302s *to itself*.
Following redirects loops until the client gives up with an error carrying no
usable response. The client therefore never follows redirects — a Voyager call
has no legitimate 3xx, so any redirect means the session was refused.

### 3.2 Durability is about cookie-jar completeness

The most important practical finding, and an expensive one — we first concluded
the opposite (§4.1).

Same account, same client, same pacing. Only the jar differed:

| Jar | Result |
| --- | --- |
| `li_at` + `JSESSIONID` (2 cookies) | killed after **~5 requests** |
| Complete browser jar (21 cookies) | **125+ requests over hours**, zero rejections, still alive the next day |

The decisive cookie is **`li_rm`**, the persistent "remember me" token — present
only when *"Keep me signed in"* was ticked at login. Every durable session
carried it; every short-lived one did not.

Hence `liapi capture` harvests the whole jar, and `status` reports tiers rather
than a bare alive/dead.

### 3.3 Pacing and call patterns do not matter

Three traffic shapes, against a (then incomplete) jar:

| Pattern | Survived |
| --- | --- |
| Back-to-back requests | ~3 |
| One call per minute | ~4 |
| Browser-shaped bursts (page load → XHR burst → idle) | ~5 |

Spreading the same calls across a minute bought nothing: the limit was a request
*count*, not a rate. Once the jar was complete, none of these patterns hit a
limit at all. Jittered pacing is retained because it is cheap and makes traffic
look human — **not** because it demonstrably extends a session.

### 3.4 Cookies are not rotated per request

A working hypothesis was that LinkedIn re-issues a cookie on every response to
re-verify, and that we were discarding it. Disproved: across **22 Voyager
responses captured from a real browser**, and every successful call from our own
client, there were **zero `Set-Cookie` headers**. The only one ever observed was
the `delete me` kill.

### 3.5 The legacy `profileView` endpoint is retired

Nearly every public example uses
`/identity/profiles/{public_id}/profileView`. It now returns **HTTP 410 Gone**.

Probing candidates against a live session:

| Endpoint | Result |
| --- | --- |
| `/identity/profiles/{id}/profileView` | **410 Gone** |
| `/identity/dash/profiles?q=memberIdentity&memberIdentity={id}` | 200 — but a 1-entity stub |
| …the same **+ `decorationId=…FullProfileWithEntities-101`** | **200, 33 entities** |

**The `decorationId` is the whole trick.** Rest.li "decorations" declare how much
of the entity graph to inline. Without one you get a stub; with the right one, a
single call returns the profile plus its positions, educations, companies,
schools, geo and industry.

### 3.6 Skills and certifications live behind GraphQL

The decoration inlines *collections* for skills, certifications and languages
but leaves them empty (`paging.total: 0`). They come from persisted GraphQL
queries instead:

```
/graphql?variables=(profileUrn:{urn},sectionType:skills)
        &queryId=voyagerIdentityDashProfileComponents.<hash>
```

These return **rendered UI components**, not entities — readable text sits at
`entityComponent.titleV2.text.text`. An empty section returns an
`emptyStateComponent` ("Nothing to see for now").

**How the entries nest is not fixed**, and this cost us a wrong conclusion. A
short section puts its entries directly under `elements`; a long one wraps them
in a `pagedListComponent` that arrives via `included` rather than inline; skills
arrive inside a `tabComponent`. A parser that only reads the top level sees
nothing and cannot tell that apart from an genuinely empty section — which is
exactly what happened (§4.4). Walking the component tree for every
`entityComponent`, across both `data` and `included`, handles all three layouts
without hard-coding a shape LinkedIn changes freely.

Which field carries the detail also varies: a certification puts its issuer in
`subtitle`, a language puts its proficiency in `caption`.

Both the decoration version suffix and the `queryId` hash **rotate when LinkedIn
ships**, so they are isolated as named constants in `app/session.py`.

### 3.7 Parsing the normalized response

Voyager returns Rest.li's normalized form: `{data, included: […]}`, where every
entity carries a `$type` and an `entityUrn`, and references between entities are
URN strings on `*`-prefixed keys. Parsing indexes `included` by URN and resolves
through it.

Three things the payload does not make obvious:

- **Location takes two hops.** `profile.geoLocation.*geo` → a `Geo`
  (`defaultLocalizedName`), whose `*country` → another `Geo` for the country.
- **There is no "current role" flag.** A position is current iff its `dateRange`
  has **no `end`**.
- **Profile pictures use one of two keys.** `displayImageReference` (the cropped
  photo actually shown) or `originalImageReference` (the raw upload) — which is
  present varies by profile. URLs are `rootUrl` plus the chosen artifact's
  `fileIdentifyingUrlPathSegment`, and are **signed with an expiry**.

### 3.8 A fingerprint inconsistency we were shipping

`curl_cffi`'s impersonation already supplies `sec-ch-ua*`, `user-agent`,
`priority` and fetch-metadata headers — but with **document navigation** values:

```
sec-fetch-dest: document    sec-fetch-mode: navigate
sec-fetch-site: none        upgrade-insecure-requests: 1
```

Every API call carried those. The browser capture shows a real XHR sends:

```
sec-fetch-dest: empty       sec-fetch-mode: cors
sec-fetch-site: same-origin priority: u=1, i
```

We were announcing a top-level page navigation while asking for JSON — something
no browser does. The client now sends document headers for the warmup page load
and XHR headers for API calls.

The same capture showed `x-li-track` was stale (`clientVersion 1.13.36` vs the
live `1.13.46312`), and that its timezone and screen fields describe *the
capturing machine*. Those had been hardcoded from one capture; they now travel
in `auth.json` beside the cookies they belong with.

---

## 4. What we got wrong

Two conclusions were stated confidently and later disproved by our own
measurements. They are recorded because the corrections are more informative
than the original claims.

### 4.1 "The account is flagged; no technique can rescue it"

After sessions died at ~3–5 requests across every traffic pattern we tried, we
concluded the account was permanently flagged, and that replaying a cookie from
a different client than the one that minted it was inherently doomed.

**Disproved by:** capturing a fresh jar in a real browser and replaying it
through our own `curl_cffi` client — **8/8 calls HTTP 200**, then 125+ over
hours, on *the same account*.

The real variable was jar completeness (§3.2). The failures came from an
8-cookie DevTools paste missing `li_rm` — not fingerprinting, not a flag.

The lesson generalises: when several experiments fail identically, suspect the
shared input before concluding the environment is hostile.

### 4.2 "The kill is rate-based"

Framed first as a rate limit, then as a request budget. Both were artefacts of
the same incomplete jar. With a complete one, none of the three traffic patterns
hit a limit at all (§3.3).

### 4.3 Our own tooling was spending the budget

Every CLI command called `is_alive()`, which hits `/me`. A normal sequence
(`login`, `status`, `serve`) burned three probes before any real work — a
meaningful share of the very budget we were trying to measure. `serve` and
`/health` no longer probe by default; liveness is left to the first real
request.

### 4.4 "Skills are gated by network degree"

Every profile we tested returned no skills, certifications or languages. Since
the test account had no connections, we concluded LinkedIn was gating those
sections by network degree, and documented it as an unresolved limitation.

**Disproved by:** testing a profile known to have them. The response was not an
empty state at all — it carried `tabComponent` and `pagedListComponent`
wrappers our parser did not look inside. The data had been there the whole
time: 31 skills, 19 certifications, 3 languages.

The failure mode is worth naming: our parser returned `[]` both when a section
was genuinely empty **and** when we could not read its layout. Those two cases
were indistinguishable from the outside, so a bug looked exactly like a
platform restriction. Logging the component *kinds* we encountered — rather
than only the entries we successfully extracted — is what made it obvious.

Same shape of mistake as §4.1: blame the environment, when the shared input was
ours.

---

## 5. What the service does as a result

| Finding | Implementation |
| --- | --- |
| §3.1 kill mechanism | detect `li_at=delete me` → `SessionKilled`; never follow redirects |
| §3.2 jar completeness | `capture` harvests the full jar; `auth.json` stores it; `status` flags a missing `li_rm` |
| §3.3 pacing | jittered delays, page warmup, bursts within a "page view" |
| §3.5 `decorationId` | one call per profile; decoration pinned as a named constant |
| §3.6 GraphQL sections | separate best-effort calls; `queryId` isolated |
| §3.7 normalized parsing | URN index and reference resolution in `app/models.py` |
| §3.8 client consistency | impersonate the *captured* browser; XHR vs document headers; `x-li-track` from the capture |

---

## 6. Honest limits

- **Sessions still expire.** Logging out of the source browser invalidates
  `li_at`, as can LinkedIn itself. Recapture is part of operating this.
- **Endpoint drift is guaranteed.** Voyager is undocumented; the decoration
  version and `queryId` hashes will move.
- **Volume is untested.** Everything here was measured at low volume from one
  residential IP. A datacenter IP or higher throughput may behave differently.
- **Single-account sample.** The durability numbers come from one account over
  roughly a day — consistent and reproducible within that scope, but not a
  general law.
