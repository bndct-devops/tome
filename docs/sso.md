# Single Sign-On (OIDC)

Authenticate to Tome with an external OpenID Connect provider — [Pocket ID](https://pocket-id.org),
Authelia, Authentik, Keycloak, Zitadel, Google, and others. SSO is **additive and off by default**:
local username/password login always stays, and at least one local admin remains a break-glass login
regardless of your IdP's state.

---

## How it works

Tome acts as an OIDC relying party. "Sign in with SSO" redirects to your IdP; after the user
authenticates, the IdP redirects back and Tome mints its normal session token — so the API, OPDS, the
KOReader plugin, and API tokens are all unchanged. The IdP's `groups` claim maps to a Tome role
(admin / member / guest).

## 1. Register Tome at your IdP

1. Create an OIDC client named e.g. `Tome`.
2. Callback / redirect URL: `https://tome.example.com/api/auth/oidc/callback` (your public Tome URL + that path).
3. Keep it a **confidential** client (has a secret) and enable **PKCE**.
4. *Optional* — set a client logo: use `https://tome.bndct.sh/tome-icon.png`, or download it and host it yourself.
5. Copy the **Client ID** and **Client Secret**.
6. Create groups for your roles — e.g. `tome_admins`, `tome_members` — and assign users. If your IdP
   gates access per-client (Pocket ID's "Allowed User Groups"), include those groups so the `groups`
   claim is emitted.

> **Pocket ID:** a group's **Name** field (not the Friendly Name) is what lands in the `groups` claim —
> match it exactly to your Tome config.

## 2. Configure Tome

Set these environment variables and restart:

```
TOME_OIDC_ENABLED=true
TOME_OIDC_ISSUER=https://auth.example.com
TOME_OIDC_CLIENT_ID=<from your IdP>
TOME_OIDC_CLIENT_SECRET=<from your IdP>
TOME_OIDC_ADMIN_GROUP=tome_admins
TOME_OIDC_MEMBER_GROUP=tome_members
TOME_OIDC_DEFAULT_ROLE=guest
# Behind a reverse proxy, pin your public origin (see below):
TOME_PUBLIC_URL=https://tome.example.com
```

| Variable | Purpose |
|---|---|
| `TOME_OIDC_ENABLED` | Master switch (default `false`). |
| `TOME_OIDC_ISSUER` | IdP base URL. Tome appends `/.well-known/openid-configuration`. |
| `TOME_OIDC_CLIENT_ID` / `_SECRET` | Credentials from the IdP client. |
| `TOME_OIDC_REDIRECT_URL` | Explicit callback URL. Optional — derived from `TOME_PUBLIC_URL` / request origin otherwise. |
| `TOME_OIDC_ADMIN_GROUP` / `_MEMBER_GROUP` / `_GUEST_GROUP` | Group → role mapping. |
| `TOME_OIDC_DEFAULT_ROLE` | Role when no group matches (default `guest`). |
| `TOME_OIDC_ROLE_SYNC` | `login` (IdP is truth every login) or `create` (set once, then editable). Default `login`. |
| `TOME_OIDC_AUTO_CREATE` | Provision unknown IdP users on first login (default `true`). |
| `TOME_OIDC_ALLOWED_GROUP` | If set, membership is required to sign in at all. |
| `TOME_OIDC_GROUPS_CLAIM` | Claim carrying group membership (default `groups`). |
| `TOME_OIDC_BUTTON_LABEL` | Login-page button text (default `Sign in with SSO`). |

A "Sign in with SSO" button then appears on the login page.

## Group → role mapping

On each login Tome reads the `groups` claim and resolves a role, admin first: admin group → `admin`,
member group → `member`, guest group → `guest`, otherwise `TOME_OIDC_DEFAULT_ROLE`.

With `ROLE_SYNC=login`, group changes at the IdP apply on the next login — **but only to SSO
accounts**. A local admin's role is never changed by the IdP, so a misconfigured group can't lock you
out.

## Adding SSO to an existing account

To keep your existing account (library, progress, shelves) instead of creating a second one:

1. Sign in to your existing account with your **password** (not SSO).
2. **Settings → Single Sign-On → Link SSO**, then authenticate at your IdP.
3. From then on, "Sign in with SSO" lands you in that same account; your password keeps working too.

> **Link first.** If you sign in with SSO *before* linking, Tome auto-provisions a brand-new account.
> Tome only auto-links by email when the IdP marks the email `verified` — many providers (including
> Pocket ID) don't, which is why explicit linking exists.

## Behind a reverse proxy

Behind a TLS-terminating proxy, the app server sees plain `http` internally, so Tome could hand your
IdP an `http://` redirect that won't match the `https://` one you registered. Fix it with **one** of:

- `TOME_PUBLIC_URL=https://tome.example.com` (recommended — also pins the KOReader plugin URL), or
- `TOME_OIDC_REDIRECT_URL=https://tome.example.com/api/auth/oidc/callback`, or
- ensure your proxy forwards `X-Forwarded-Proto: https` (Tome honors it).

The resulting callback (`<public-url>/api/auth/oidc/callback`) must **exactly** match a callback
registered in your IdP client.

## Logout

Logging out of Tome clears the Tome session only — it does not end your IdP session. To fully sign
out, also log out at your identity provider.
