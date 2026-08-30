# LinkedIn Profile API

An HTTP API that takes a LinkedIn profile URL and returns the profile as
structured JSON.

It is **reverse-engineered**: the service calls LinkedIn's own private Voyager
API directly over HTTP, authenticating as a logged-in user. No browser, no
headless automation, no HTML scraping at request time — the upstream response is
already JSON.

📓 **[How this was reverse-engineered](docs/reverse-engineering.md)** — how
LinkedIn kills a session, what actually makes one durable, why the endpoint every
public example uses now returns 410, and the conclusions we got wrong along the
way.

```bash
curl https://<host>/profile/williamhgates
```

```json
{
  "public_id": "williamhgates",
  "full_name": "Bill Gates",
  "headline": "Chair, Gates Foundation and Founder, Breakthrough Energy",
  "location": "Seattle, Washington, United States",
  "country": "United States",
  "industry": "Philanthropy",
  "picture_url": "https://media.licdn.com/dms/image/…",
  "experience": [
    {
      "title": "Co-chair",
      "company": "Gates Foundation",
      "company_logo": "https://media.licdn.com/…",
      "employment_type": null,
      "location": null,
      "description": null,
      "start": { "month": null, "year": 2000 },
      "end": null,
      "is_current": true
    }
  ],
  "education": [
    { "school": "Harvard University", "degree": null, "field_of_study": null,
      "start": { "month": null, "year": 1973 }, "end": { "month": null, "year": 1975 } }
  ],
  "skills": ["Python (Programming Language)", "Kubernetes", "…"],
  "certifications": [
    { "name": "Certified Kubernetes Application Developer",
      "authority": "The Linux Foundation", "license_number": null, "url": null }
  ],
  "languages": [
    { "name": "English", "proficiency": "Professional working proficiency" }
  ]
}
```

## Quick start

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra capture          # install (the extra adds the capture browser)
uv run playwright install chromium
source .venv/bin/activate

liapi capture                    # log in once; writes auth.json
liapi status                     # confirm the session works
liapi serve                      # http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000/docs> for interactive docs, or:

```bash
curl 'http://127.0.0.1:8000/profile/williamhgates'
```

## Authentication

The service acts as a logged-in LinkedIn user. That session lives in
**`auth.json`**, beside `pyproject.toml` at the project root — cookies *plus*
the client context they were captured with (user agent, locale, and the
`x-li-track` telemetry blob). It is found from any working directory; set
`AUTH_FILE` to override.

`liapi capture` opens a browser once so it can harvest all of it; a cookie paste
cannot, because most of it only appears in request headers. **This is a
setup-time step** — Playwright is an optional extra and the API never launches a
browser. You can also write `auth.json` by hand.

Full details, including where every field comes from in DevTools:
**[docs/auth.md](docs/auth.md)**.

> `auth.json` is a live credential. It is gitignored and written `0600`; never
> commit it.

## API

### `GET /profile/{url_or_username}`

Accepts a bare vanity slug or a full profile URL pasted inline:

```
/profile/williamhgates
/profile/https://www.linkedin.com/in/williamhgates/
```

| Query param | Default | Meaning |
| --- | --- | --- |
| `sections` | `true` | Also fetch skills, certifications and languages. Each is a separate upstream call; `false` is noticeably faster. |
| `refresh` | `false` | Bypass the cache and re-fetch. |

**Response** — the `Profile` object. Every field is always present (`null` or
`[]` when unknown) and dates are structured, not display strings:

| Field | Type |
| --- | --- |
| `public_id`, `profile_urn`, `member_urn` | `string \| null` |
| `first_name`, `last_name`, `full_name` | `string \| null` |
| `headline`, `summary` | `string \| null` |
| `location`, `country`, `industry` | `string \| null` |
| `picture_url`, `background_url` | `string \| null` |
| `experience[]` | `title, company, company_logo, employment_type, location, description, start{month,year}, end{month,year}, is_current` |
| `education[]` | `school, school_logo, degree, field_of_study, grade, activities, start, end` |
| `skills[]` | `string` |
| `certifications[]` | `name, authority, license_number, url` |
| `languages[]` | `name, proficiency` |

**Errors**

| Status | Meaning |
| --- | --- |
| `422` | Not a valid LinkedIn profile URL or public id |
| `502` | Upstream error — LinkedIn returned something unexpected |
| `503` | No usable session, or LinkedIn invalidated it. Run `liapi capture` |

### `GET /health`

Reports service and session state without calling LinkedIn, so it is safe to
poll. Add `?check=true` to actively probe the session.

```json
{ "status": "ok", "session": "configured",
  "account": "your-slug", "captured_hours_ago": "19.3" }
```

## CLI

```
liapi capture   log in through a browser, write auth.json
liapi status    what the credential holds, and whether it still works
liapi serve     run the API
```

## How it works

LinkedIn's website is a single-page app that fetches its data from a private
JSON API at `www.linkedin.com/voyager/api/…`. This service calls that API the
same way the site's own JavaScript does, authenticating with a session cookie.
The response is already JSON — there is nothing to render or scrape.

A profile is one call to the dash collection, and the `decorationId` is what
makes it useful: without it the response is a stub, with it the profile arrives
alongside its positions, educations, companies, schools, geo and industry.
Skills, certifications and languages come from separate GraphQL queries.
Results are cached on disk so repeat requests cost nothing upstream.

**Documentation**

| | |
| --- | --- |
| [Architecture](docs/architecture.md) | how it is built: modules, request flow, parsing, caching, design decisions |
| [Authentication](docs/auth.md) | what `auth.json` holds, and how to obtain it — by capture or by hand |
| [Reverse engineering](docs/reverse-engineering.md) | how the API was discovered, what was measured, and what we got wrong |

## Known limitations

- **Sections cost extra calls.** Skills, certifications and languages are not
  inlined in the profile response; each needs its own request. Pass
  `?sections=false` to skip them when you only need the core profile. Results
  are cached on disk for an hour, so this is a first-fetch cost.
- **Sessions expire.** `li_at` is long-lived, but logging out of the source
  browser invalidates it, as can LinkedIn itself. `liapi status` reports this;
  recapture to recover.
- **Capture completeness matters more than anything else.** A jar holding only
  `li_at` + `JSESSIONID` was killed after ~5 requests; a complete one served
  125+ requests over hours. `li_rm` — set only when "Keep me signed in" is
  ticked — is the difference. `capture` and `status` both flag this.
- **Endpoint drift.** Voyager is undocumented. The `decorationId` version and
  the GraphQL `queryId` hashes rotate when LinkedIn ships; they are isolated as
  constants in `app/session.py` and can be re-captured from a browser session.
- **Visibility.** Only data the authenticated account can see is returned.
- **Rate limits.** Requests are paced deliberately and profiles cached for an
  hour. This is built for low volume, not bulk extraction. Use a throwaway
  account.
- **Untested from a datacenter IP.** Everything here was measured from a
  residential connection; a deployed instance may be treated differently.

## Development

```bash
uv sync                # runtime + dev; omit --extra capture for a browserless install
pytest                 # offline unit tests
```

Tests are offline — no network or credential needed. Module layout and design
decisions are in [docs/architecture.md](docs/architecture.md).

