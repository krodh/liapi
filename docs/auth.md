# `auth.json` — the credential file

> How the credential is used at request time: [architecture.md](./architecture.md).

The API authenticates as a logged-in LinkedIn user. Everything it needs lives in
one file, **`auth.json`, beside `pyproject.toml` at the project root**. It is
found from any working directory, so the CLI behaves the same wherever you run
it. Set `AUTH_FILE` to point somewhere else — that path resolves against your
working directory.

A LinkedIn session is **not just a cookie**. The session token is bound to the
client that minted it, so replaying it convincingly needs that client's identity
too — its user agent, locale, and the `x-li-track` blob carrying LinkedIn's
client version plus your machine's timezone and screen size. `auth.json`
captures all of it.

> This file is a live credential. It is gitignored, written `0600`, and must
> never be committed.

## The easy way

```bash
uv sync --extra capture && playwright install chromium   # once
liapi capture
```

A browser opens; log in **with "Keep me signed in" ticked**. The command
harvests the cookies and the client context, verifies the session against
LinkedIn, and writes `auth.json`.

`liapi capture` is a **setup-time** tool. The API never launches a browser —
Playwright is an optional extra that the running service does not import.

## The manual way

Everything can be gathered by hand from DevTools. Log in at
<https://www.linkedin.com/login> (tick "Keep me signed in"), open DevTools
(F12) → **Network**, click any request to `linkedin.com`, and read its
**Request Headers**.

### Shape

```json
{
  "version": 1,
  "captured_at": "2026-08-30T13:45:12Z",
  "account": {
    "public_id": "your-vanity-slug",
    "member_urn": "urn:li:member:914999167"
  },
  "client": {
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "impersonate": "chrome150",
    "accept_language": "en-US,en;q=0.9",
    "li_lang": "en_US",
    "li_track": {
      "clientVersion": "1.13.46312",
      "mpVersion": "1.13.46312",
      "osName": "web",
      "timezoneOffset": 5.5,
      "timezone": "Asia/Calcutta",
      "deviceFormFactor": "DESKTOP",
      "mpName": "voyager-web",
      "displayDensity": 1,
      "displayWidth": 1280,
      "displayHeight": 720
    }
  },
  "cookies": {
    "li_at": "AQEDAT…",
    "JSESSIONID": "\"ajax:1234567890\"",
    "li_rm": "AQE…",
    "bcookie": "\"v=2&…\"",
    "bscookie": "\"v=1&…\"",
    "lidc": "\"b=…\"",
    "__cf_bm": "…"
  }
}
```

Only `cookies` is mandatory — every other field has a sensible default. A bare
cookie mapping is also accepted:

```json
{ "li_at": "AQEDAT…", "JSESSIONID": "\"ajax:1234567890\"" }
```

…but see **Why the client block matters** below before relying on that.

### Where each field comes from

| Field | Where to find it |
| --- | --- |
| `cookies` | The `cookie:` **request header** (right-click → Copy value), or Application → Cookies → `https://www.linkedin.com`. Copy **all** of it. |
| `client.user_agent` | The `user-agent` request header, or run `navigator.userAgent` in the console. |
| `client.impersonate` | Pick the `chromeNNN` target at or below your Chrome major version: `chrome150`, `146`, `145`, `142`, `136`, `133`, `131`, `124`. Chrome 151 → `chrome150`. |
| `client.accept_language` | The `accept-language` request header. |
| `client.li_lang` | The `x-li-lang` request header (usually `en_US`). |
| `client.li_track` | The `x-li-track` request header — it is already JSON, paste it verbatim. |
| `account.public_id` | Your profile URL slug: `linkedin.com/in/<this>`. Informational. |
| `account.member_urn` | From `/voyager/api/me`. Informational; omit it. |
| `captured_at` | ISO-8601 UTC, e.g. `2026-08-30T13:45:12Z`. Used to report session age. |

### Which cookies matter

| Tier | Cookies | Consequence |
| --- | --- | --- |
| **Required** | `li_at`, `JSESSIONID` | Nothing authenticates without both. `li_at` is the credential; `JSESSIONID` is also the CSRF token. |
| **Durability** | `li_rm` | The persistent "remember me" token, set **only if "Keep me signed in" was ticked**. Sessions without it died after ~5 requests in testing; sessions with it served 125+ over hours. |
| **Recommended** | `bcookie`, `bscookie`, `lidc`, `__cf_bm` | Browser identifiers, datacenter routing and the Cloudflare token. Without them the jar looks unlike a real browser. |

Anything else you copy is kept and sent, which is good — a fuller jar looks more
like a real browser. Nothing else is checked.

## Why the client block matters

Suppose you capture a session in London on a 4K screen but leave the defaults in
place. The API would then present *your* cookies alongside a claim that you are
in UTC on a 1920×1080 display, running a LinkedIn client version you never used.
That mismatch is exactly the kind of inconsistency that gets a session flagged.

Filling in `client` from the same browser that produced the cookies keeps the
story straight. If you omit it, the defaults are a plausible desktop client —
workable, but less convincing than the truth.

## Checking it

```bash
liapi status
```

```
source:   /path/to/project/auth.json
account:  your-vanity-slug
captured: 19.3h ago
cookies:  21
client:   chrome150  (Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Appl…)
session:  alive
```

`status` names any missing tier and exits non-zero if the session is unusable or
expired. `GET /health` reports the same state over HTTP (add `?check=true` to
actively probe LinkedIn rather than just reading the file).

When the session expires — logging out of the source browser will do it —
capture again.
