# Security policy

OmniSync holds the keys to your cloud accounts (OAuth tokens in
`rclone.conf`) and can delete files on both sides of a sync, so we treat
security reports with priority.

## Supported versions

OmniSync is before 1.0: only the latest release gets security fixes.

| Version | Supported |
| --- | --- |
| the latest release | yes |
| older releases | no: upgrade (database migrations run on start) |
| `main` between releases | fixed there first, then released |

The latest release is on the
[releases page](https://github.com/pan-fire/OmniSync/releases).

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a
security problem.**

Report it privately through GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/pan-fire/OmniSync/security/advisories/new)
(on the repository's **Security** tab, under **Advisories**). Only you and
the maintainers see the report, and we work on the fix, the advisory and a
coordinated release with you there.

Please include:

- the OmniSync version (`GET /health` → `"version"`, the web UI's sidebar,
  or `osync --version`) and how it is deployed (docker compose, released
  images, without Docker; behind which reverse proxy, if any);
- what an attacker needs (network position, a token, a logged-in browser,
  a malicious remote or file) and what they gain;
- steps to reproduce or a proof of concept.

Never include real tokens, `rclone.conf` contents, passwords or personal
files; redact them.

What to expect:

- an acknowledgement within 7 days;
- an assessment (whether we can reproduce it, how severe it is) and a plan
  within 14 days;
- a fix released as soon as it is ready, with a GitHub security advisory
  (and a CVE where it applies) published together with the release.

Please keep the problem private until the fix is released, or for 90 days
after your report if that comes first; tell us if you need a different
timeline. We credit reporters in the advisory and the changelog unless you
prefer otherwise. OmniSync is maintained by volunteers and has no bug
bounty.

## Scope

In scope: the backend API, the web UI and its server-side proxy, the `osync`
terminal client, the Docker images and compose files in this repository, and
the release pipeline (images, binaries, signatures). Examples: bypassing the
API token or the web UI's host/origin checks, reading or exfiltrating
`rclone.conf`, the API token or notification credentials, path traversal
outside the allowed folders, making OmniSync delete or overwrite files
without the safety checks, command or option injection into rclone.

Out of scope: vulnerabilities in rclone, cloud providers, Docker or other
upstream software (report them upstream; tell us if OmniSync needs to react),
attacks that require an attacker who already has the API token or full
access to the host, and deployments that expose the web UI or the API to a
network without the protection described below.

## Security model

OmniSync is a single-user, self-hosted tool. Its defaults assume that
everyone who can reach it is you.

- **Loopback by default.** Docker compose publishes the API (8000) and the
  web UI (3000) on `127.0.0.1` only; run directly, the backend binds
  `127.0.0.1`. Nothing is reachable from the network until you change that.
- **API token.** Every API request except `GET /health` and the OAuth
  callback needs `Authorization: Bearer <OMNISYNC_API_TOKEN>`. Use at least
  32 random characters (`openssl rand -hex 32`). Repeated wrong tokens from
  one address are rate limited.
- **The web UI holds the token, not the browser.** Its server adds the token
  to every `/api` request, so anyone who can open the UI has full control.
  The UI refuses `/api` requests for unknown `Host` names (DNS rebinding)
  and state-changing requests from other origins (CSRF), and sends a strict
  Content-Security-Policy.
- **Logging in to the web UI.** Before you make the UI reachable from other
  machines, put a login in front of it: the UI's optional built-in login
  (`OMNISYNC_UI_PASSWORD_HASH`: one password, HttpOnly SameSite=Strict
  session cookies, throttled failed logins) and/or a reverse proxy with TLS
  and authentication (basic auth, an SSO proxy, or a VPN). Use HTTPS either
  way: the login does not encrypt traffic. See
  [Exposing the web UI / HTTPS](README.md#exposing-the-web-ui--https).
- **Host names.** The backend accepts only `localhost`, `127.0.0.1`, `::1`
  (and `backend` in compose) unless `OMNISYNC_ALLOWED_HOSTS` adds names.
- **Folders.** Profiles, backup targets and restores are limited to
  `OMNISYNC_BROWSE_ROOTS` (in compose: the one mounted sync folder) and may
  never include OmniSync's own data directory.
- **Secrets at rest.** `rclone.conf` (OAuth tokens and keys), the generated
  API token (mode 0600) and `config.toml` (0600 once it holds notification
  credentials) live unencrypted in the data volume, which only the backend
  container mounts. Anyone with root on the host can read them; back the
  volume up as privately as the accounts it can reach.
- **Containers.** The backend drops to `PUID`/`PGID` after fixing the data
  volume's owner, with all capabilities dropped except the four that needs
  and `no-new-privileges`; the web UI runs read-only as an unprivileged user.
  Do not set `PUID=0`. The opt-in `docker-compose.dbus.yml` trades AppArmor
  confinement for desktop notifications; use it only on a desktop.
- **Releases.** Images and the binaries' `SHA256SUMS` are signed with cosign
  (keyless) and carry an SBOM and provenance; see
  [Verify a release](README.md#verify-a-release).

### Exposing OmniSync safely

- Keep the API on loopback; only the web UI needs to be reachable, and only
  through a reverse proxy on the same host that terminates TLS and requires
  a login (or over a VPN).
- Set `OMNISYNC_UI_ALLOWED_HOSTS` (and, if clients other than the UI use the
  API, `OMNISYNC_ALLOWED_HOSTS`) to exactly the names you use.
- Never publish port 3000 or 8000 directly on a LAN or the internet.
- Keep OmniSync and its images up to date.

## Handling secrets (for contributors)

A sync tool's repository must never carry anyone's credentials.

- Never commit `.env`, `rclone.conf` (yours or OmniSync's), API tokens,
  OAuth tokens, notification credentials (webhook headers, ntfy tokens,
  SMTP passwords), Web Push keys or the data directory (`/data/omnisync`
  in the container, or wherever the `OMNISYNC_*_PATH` settings point when
  you run the backend directly) with its database, logs and `config.toml`.
  `.gitignore` covers `.env` only, so check `git status` before you
  commit.
- Use made-up values in tests, fixtures, docs and screenshots
  (`example.com`, `test-token`), never values from a real account.
- Install the repository's pre-commit hooks once per clone with
  `pre-commit install`. They include a secret scanner that refuses a
  commit containing something that looks like a credential; do not bypass
  it with `--no-verify`. A finding that is not a secret goes on the
  scanner's allow list in the same pull request, with a reason.
- If a secret was committed or pushed anyway, removing it in a new commit
  is not enough: revoke or rotate it first (new token, new OAuth
  sign-in, new password), then tell the maintainers.
