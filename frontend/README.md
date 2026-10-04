# OmniSync web UI

The Next.js 16 (App Router, React 19, Tailwind CSS 4, shadcn/ui, TanStack
Query) front end for the OmniSync backend. See the [main README](../README.md)
for what OmniSync does and how to run the whole stack.

## How it talks to the backend

The browser only ever calls `/api/*` on this server. `src/proxy.ts` forwards
each request to `${BACKEND_URL}/*` and sets `Authorization: Bearer
${OMNISYNC_API_TOKEN}`, replacing any header the browser sent, so the token
stays on the server. Both variables are read per request, so a built image
works against any backend:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Backend base URL |
| `OMNISYNC_API_TOKEN` | (none) | The backend's API token |
| `OMNISYNC_UI_ALLOWED_HOSTS` | (none) | Extra `Host` names (optionally `:port`) besides `localhost`, `127.0.0.1` and `[::1]` |
| `OMNISYNC_UI_PASSWORD_HASH` | (none) | Turns the optional login on (`node scripts/hash-password.mjs` makes it) |
| `OMNISYNC_UI_PASSWORD` | (none) | Plain-text alternative to the hash (warns) |
| `OMNISYNC_UI_SESSION_SECRET` | from the hash | Session signing key, 32+ characters |
| `OMNISYNC_UI_SESSION_DAYS` | `7` | Sliding session lifetime |

Before adding the token, `src/proxy.ts` answers 403 for other `Host` names,
for state-changing requests whose `Origin` (or, without one,
`Sec-Fetch-Site`) is not this UI, and for request bodies that are not
`application/json`.

## Login

Off by default. With `OMNISYNC_UI_PASSWORD_HASH` set, `src/proxy.ts` runs
on every request except static files and checks the session cookie first:
pages without one redirect to `/login?next=<page>`, `/api` answers 401
with `{"detail": {"code": "login_required"}}` (`apiFetch` then sends the
browser to the login page). The proxy itself answers `POST /auth/login`
(`{"password": "..."}`, JSON, same origin), `POST /auth/logout`,
`GET /auth/session` and `GET /healthz`. The code is in `src/lib/auth/`:

| File | What |
| --- | --- |
| `config.ts` | Settings from the environment, and the design notes |
| `password.ts` | scrypt hashes (`node:crypto`; same format as `scripts/hash-password.mjs`) |
| `session.ts` | HMAC-signed session tokens and the logout list |
| `throttle.ts` | Per-client backoff and the global failure budget |
| `gate.ts` | The checks and endpoints `src/proxy.ts` calls |
| `next-path.ts` | `?next=` validation (same-site paths only) |
| `client.ts` | Browser side: redirect on 401, logout |

Everything is in memory in the server process, so run one UI server per
backend (as compose does). Users, setup and the security trade-offs are
described in
[Exposing the web UI / HTTPS](../README.md#exposing-the-web-ui--https).

Proxied requests may take up to 20 minutes (`experimental.proxyTimeout` in
`next.config.ts`): the longest call that still waits for its result is a
check or diff, which the backend bounds by `OMNISYNC_CHECK_TIMEOUT` (15
minutes by default). Syncs, backups, restores and per-file actions run in
the background: starting one answers at once, and the UI polls its job.

## Development

Node 24 (LTS) and pnpm 10, with a backend running on `127.0.0.1:8000`:

```bash
pnpm install
OMNISYNC_API_TOKEN=<token> pnpm dev   # http://127.0.0.1:3000
```

| Command | What it runs |
| --- | --- |
| `pnpm lint` | ESLint (neostandard + Next rules; `pnpm lint:fix` fixes) |
| `pnpm typecheck` | `tsc --noEmit` |
| `pnpm test` | Vitest with Testing Library and fast-check |
| `pnpm build` | Production build (`output: 'standalone'`) |

Code lives in `src/`: routes in `app/`, UI in `components/`, data hooks in
`hooks/`, the API client in `lib/api.ts` and its types in `types/`. UI text
is translated in `i18n/locales` (`en`, `de`, `fa`); a test checks that every
key the code uses exists in all three.

## Production image

`Dockerfile` builds the standalone server in stages (pinned pnpm, lockfile
install, `next build`) and runs `node server.js` as the unprivileged `node`
user on port 3000:

```bash
docker build -t omnisync-frontend .
docker run --rm -p 127.0.0.1:3000:3000 \
  -e BACKEND_URL=http://<backend>:8000 -e OMNISYNC_API_TOKEN=<token> omnisync-frontend
```

The backend must accept the host name in `BACKEND_URL`
(`OMNISYNC_ALLOWED_HOSTS`). `docker-compose.yml` in the repository root runs
it next to the backend.
