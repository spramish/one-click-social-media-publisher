# OAuth Setup — Connecting Social Accounts

Author: **Pramish Sharma**

> **Phase 6 reference.** Everything an operator must do *outside* this codebase
> before a platform can be connected: the developer application, the exact
> redirect URI, the permissions requested and why, and the app review /
> publishing audit / account eligibility each platform enforces.
>
> Nothing here is optional — a platform whose client credentials are missing
> simply reports `configured: false` on `GET /api/v1/accounts/{workspace_id}/capabilities`
> and its **Connect** button stays disabled. No connection is ever faked.

---

## 1. How the flow works

The publisher uses the **OAuth 2.0 authorization-code** flow. It is the only
flow all four platforms support for server-side publishing, and it never asks a
user for a platform password.

```
Browser                API (/api/v1/accounts)             Platform
   │                            │                             │
   │ POST {ws}/{platform}/authorize                            │
   │───────────────────────────>│  create 256-bit state       │
   │                            │  (store SHA-256 digest,     │
   │                            │   PKCE verifier encrypted)  │
   │   authorization_url        │                             │
   │<───────────────────────────│                             │
   │  redirect                                                │
   │─────────────────────────────────────────────────────────>│  user consents
   │  GET /accounts/{platform}/callback?code=&state=           │
   │───────────────────────────>│  validate + consume state   │
   │                            │  exchange code → tokens     │
   │                            │  read identity, encrypt,    │
   │                            │  upsert social_account      │
   │  302 → OAUTH_SUCCESS_REDIRECT?connected={platform}         │
   │<───────────────────────────│                             │
```

Guarantees:

- **`state` is a single-use, 256-bit secret bound to the workspace.** Only its
  SHA-256 digest is stored, it expires after `OAUTH_STATE_TTL_SECONDS`, and
  replaying it answers *"already been used"*. A `state` minted for one platform
  is refused by another.
- **PKCE (`S256`) is sent wherever the platform supports it** — mandatory on
  TikTok and used on Google; Meta's Facebook Login path does not need it.
- **Tokens are encrypted at rest** with AES-256-GCM before they touch the
  database (see §7). They are never returned to the browser.
- **The callback is deliberately unauthenticated**: the browser arrives from the
  platform with no session, and the `state` *is* the authorization.

## 2. Environment variables

| Variable | Purpose |
| --- | --- |
| `BACKEND_PUBLIC_URL` | Public origin the **browser** reaches this API on. Every `redirect_uri` is built from it and **must match the platform's registration character-for-character**. |
| `OAUTH_SUCCESS_REDIRECT` / `OAUTH_FAILURE_REDIRECT` | Where the callback sends the browser afterwards (default `http://localhost:3000/accounts`). |
| `OAUTH_STATE_TTL_SECONDS` | Lifetime of an authorization attempt (default `600`). |
| `OAUTH_HTTP_TIMEOUT_SECONDS` | Per-call platform timeout (default `30`). |
| `OAUTH_REFRESH_MARGIN_SECONDS` | Refresh a token this long before it expires (default `300`). |
| `OAUTH_ENCRYPTION_KEY` | Optional dedicated key for token encryption. Empty ⇒ derived from `SECRET_KEY`. **Set a long random value in production** so rotating `SECRET_KEY` does not orphan stored tokens. |
| `META_GRAPH_VERSION` | Graph API version segment (default `v21.0`). |
| `FACEBOOK_CLIENT_ID` / `FACEBOOK_CLIENT_SECRET` | Meta app credentials. |
| `INSTAGRAM_CLIENT_ID` / `INSTAGRAM_CLIENT_SECRET` | Optional separate Meta app; otherwise the Facebook pair is used. |
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | Google OAuth client. |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | TikTok app key/secret. |

## 3. Redirect URIs to register

Register **exactly** these strings with each platform (replace the host with
your deployed `BACKEND_PUBLIC_URL`):

| Platform | Redirect URI |
| --- | --- |
| Instagram | `{BACKEND_PUBLIC_URL}/api/v1/accounts/instagram/callback` |
| Facebook | `{BACKEND_PUBLIC_URL}/api/v1/accounts/facebook/callback` |
| YouTube | `{BACKEND_PUBLIC_URL}/api/v1/accounts/youtube/callback` |
| TikTok | `{BACKEND_PUBLIC_URL}/api/v1/accounts/tiktok/callback` |

> A single-character mismatch in scheme, host, port, or trailing slash is the
> most common cause of `redirect_uri` errors. The API always builds the URI from
> `BACKEND_PUBLIC_URL`, so there is one place to fix.

---

## 4. Instagram (Instagram Graph API via Facebook Login)

**Developer application**

1. Create a **Meta app** of type **Business** at `developers.facebook.com`.
2. Add the **Facebook Login** product, then add the Instagram redirect URI above
   under *Facebook Login → Settings → Valid OAuth Redirect URIs*.
3. Note the **App ID** and **App Secret** → `INSTAGRAM_CLIENT_ID` /
   `INSTAGRAM_CLIENT_SECRET` (or leave the Instagram pair empty to share the
   Facebook app).
4. Add the connecting accounts as **testers** while the app is unapproved.

**Permissions requested** (`INSTAGRAM_SCOPES`)

| Scope | Why |
| --- | --- |
| `instagram_basic` | Read the professional account's identity so it can be listed. |
| `instagram_content_publish` | Publish Reels/video to the account. |
| `instagram_manage_insights` | Read post metrics for the analytics phase. |
| `pages_show_list` | Enumerate the Pages that carry a linked Instagram account. |
| `pages_read_engagement` | Read the Page's engagement data. |
| `business_management` | Required to act on assets owned by a Business Manager. |

**App review / publishing audit** — Instagram publishing has **no sandbox**.
`instagram_basic` and `instagram_content_publish` require **App Review** (and
`business_management` additionally requires a **verified business**) before the
app can serve anyone outside its own admins/developers/testers. Until then, only
those users can connect.

**Account eligibility**

- The Instagram account must be **professional** — **Business or Creator**.
  Personal accounts have no API publishing access at all.
- It must be **linked to a Facebook Page** that the connecting user administers.
- Publishing needs a **publicly reachable video URL**: Instagram pulls the
  media itself, so the signed media URL must be internet-reachable (a
  `localhost` API cannot serve production Instagram publishing).
- In this implementation the connected identity is the **Instagram professional
  account**; the Page's own access token is stored as the credential.

**Notes** — Meta issues **no refresh token**. See §6.

## 5. Facebook (Meta Graph API)

**Developer application** — the same Meta app as Instagram (or its own pair).
Add the Facebook redirect URI above to *Valid OAuth Redirect URIs*.

**Permissions requested** (`FACEBOOK_SCOPES`)

| Scope | Why |
| --- | --- |
| `pages_show_list` | List the Pages the user manages. |
| `pages_read_engagement` | Read Page metadata and engagement. |
| `pages_manage_posts` | Publish video to the Page. |
| `business_management` | Act on Business-Manager-owned Pages. |
| `public_profile` | Basic identity of the authorizing user. |

**App review** — `pages_show_list`, `pages_read_engagement` and
`pages_manage_posts` require **App Review** for users outside the app's
admins/developers/testers; `business_management` requires a **verified
business**.

**Account eligibility**

- A **Facebook Page** is the publishing destination — personal profiles cannot
  publish video through the API.
- The connecting user must hold a **role on the Page** (admin or editor) and the
  Page must not be restricted in a way that blocks API publishing.
- One authorization yields **one connected account per Page**; a user who
  manages several Pages connects several accounts in a single pass, each with
  its own Page access token.

**Notes** — Meta issues **no refresh token**; the ~60-day long-lived token is
the refresh material. See §6.

---

## 6. YouTube (Google OAuth 2.0 + YouTube Data API v3)

**Developer application**

1. Create a project in the **Google Cloud Console** and enable the
   **YouTube Data API v3**.
2. Configure the **OAuth consent screen** (External, with the scopes below).
3. Create an **OAuth client ID** of type **Web application** and add the YouTube
   redirect URI above under *Authorised redirect URIs*.
4. Put the client id/secret in `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`.

**Permissions requested** (`YOUTUBE_SCOPES`)

| Scope | Why |
| --- | --- |
| `https://www.googleapis.com/auth/youtube.upload` | Upload video to the channel. |
| `https://www.googleapis.com/auth/youtube.readonly` | Read the channel identity and statistics. |

**App review / publishing audit** — `youtube.upload` is a
**sensitive/restricted** scope. The app must pass **Google OAuth verification**,
and `youtube.upload` additionally requires a **YouTube API compliance audit**
before the app can be used by the general public. Until then:

- the app can only be used by up to **100 test users** you add on the consent
  screen, and
- **uploads are locked to `private` visibility**, regardless of the requested
  privacy setting.

Plan for the audit before relying on public publishing. Quota matters too: the
default daily quota is 10,000 units and a single `videos.insert` costs ~1,600.

**Account eligibility**

- The connecting Google account must **own or manage** the target channel.
- For a **Brand Account** channel, the user selects that channel during consent;
  the connection stores the channel id, not the Google account.

**Notes** — Google is a PKCE-capable provider, so the authorization carries an
S256 challenge, and `access_type=offline` + `prompt=consent` force a **refresh
token** to be issued on every connection (so a reconnect after revocation
actually repairs the grant). Refresh tokens issued to an app still in **Testing**
publishing status expire after **7 days**, so verify the app (or expect weekly
reconnects in development).

## 7. Tokens at rest

Every access and refresh token is encrypted with **AES-256-GCM** before it is
stored. The key is derived with **HKDF-SHA256** from `OAUTH_ENCRYPTION_KEY` (or
`SECRET_KEY` under a distinct HKDF context). The stored value is:

```
v1.<base64url( nonce ‖ ciphertext ‖ GCM tag )>
```

GCM is authenticated, so a tampered or truncated ciphertext fails to decrypt and
raises an error rather than returning garbage. A key change (e.g. rotating
`SECRET_KEY` without setting `OAUTH_ENCRYPTION_KEY`) makes existing tokens
undecryptable, which the API reports as `409`/`needs_reconnect` — the user
reconnects and the new key takes over.

## 8. Token refresh & revocation matrix

| Platform | Access token | Refresh token | "Refresh" means | Revocation on disconnect |
| --- | --- | --- | --- | --- |
| YouTube | ~1 h | Yes (opaque, long-lived) | `grant_type=refresh_token` | `POST oauth2.googleapis.com/revoke` — revokes access **and** refresh token |
| TikTok | ~24 h | Yes (~365 d) | `grant_type=refresh_token` | `POST open.tiktokapis.com/v2/oauth/revoke/` |
| Facebook | ~60 d (long-lived) | **None** | re-exchange via `fb_exchange_token` | `DELETE /me/permissions` (drops the whole app grant) |
| Instagram | ~60 d (long-lived) | **None** | re-exchange via `fb_exchange_token` | `DELETE /me/permissions` |

Because Meta has no refresh token, the connector stores the long-lived token in
the *refresh* slot as well, so the generic refresh path works unchanged. A
disconnect always removes the stored credential locally; `revoked_upstream` in
the response reports whether the platform confirmed the grant is gone. When a
token cannot be refreshed (no refresh token, or the stored credential no longer
decrypts) the account is flagged `needs_reconnect` and the API answers **`409`**.

## 9. Testing without production approval

Every platform allows a limited number of unapproved users. Add yourself (and
QA accounts) before the app is reviewed:

| Platform | How to grant access to an unapproved app |
| --- | --- |
| Instagram / Facebook | App Dashboard → Roles → **Add testers** (admins, developers, testers). |
| YouTube | OAuth consent screen → **Test users** (≤ 100; refresh tokens expire in 7 days). |
| TikTok | TikTok for Developers → **Sandbox** / target users; until the audit passes, posts land in the user's drafts. |

## 10. Pre-flight checklist

For each platform you intend to enable:

1. Put the client id/secret in `backend/.env` and restart the API.
2. Register the **exact** redirect URI from §3.
3. Confirm `GET /api/v1/accounts/{workspace_id}/capabilities` reports
   `"configured": true` for it.
4. Connect a test account and confirm it appears in
   `GET /api/v1/accounts/{workspace_id}` with the expected `scopes`.
5. Exercise **Refresh** and **Disconnect**, and confirm the platform's app
   permissions page no longer lists the grant after a disconnect.
