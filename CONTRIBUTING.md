# Contributing to OmniSync

Thanks for helping. OmniSync moves and deletes people's files, so the bar
for changes that touch syncing is high: every change comes with tests, and
anything that could lose data needs a test that proves it does not.

- Bugs and feature ideas: open an issue with the templates.
- Security problems: **do not open an issue**; see [SECURITY.md](SECURITY.md).
- Larger changes: open an issue first, so we can agree on the approach
  before you write it.

## Repository layout

```text
backend/     FastAPI app (Python 3.12); tests in backend/tests, migrations in backend/migrations
frontend/    Next.js web UI (Node 24 LTS, pnpm 10)
tui/         Go terminal UI and CLI, `osync` (Go 1.25)
docs/        user guide (docs/gem) and the API error codes (docs/api-errors.md)
scripts/     container entrypoint, OpenAPI generator, release scripts (scripts/release)
```

[`AGENTS.md`](AGENTS.md) sums up the checks and conventions below for
coding agents (and is a handy checklist for people too).

### Code map

```text
 browser ──> web UI server (Next.js, :3000) ──/api + token──┐
 osync TUI/CLI (Go) ─────────────────────────── token ──────┤
                                                            v
                                  backend (FastAPI + uvicorn, :8000)
                                   ├─ SyncEngineManager: one SyncEngine per enabled profile
                                   ├─ BackupService: scheduled backups and restores
                                   ├─ NotificationDispatcher: webhook, ntfy, email, Web Push, desktop
                                   ├─ SQLite (SQLAlchemy async, Alembic migrations)
                                   └─ rclone subprocesses, own config file
```

- `backend/main.py`: app setup. On start it migrates the database, resolves
  the API token, starts one engine per enabled profile, the backup
  scheduler and the liveness monitor, and wires the route modules to these
  services.
- `backend/security.py`: the bearer-token dependency on every route (except
  `GET /health` and the OAuth callback) and the Host allow-list middleware.
- `backend/api/routes/`: one module per area (`profiles`, `backups`,
  `remotes`, `wizard`, `browse`, `jobs`, `conflicts`, `logs`,
  `notifications`, `config`, `health`); `api/schemas.py` holds the
  request/response models and their limits; `api/errors.py` the error
  envelope.
- `backend/services/sync_engine/`: the per-profile engine (watcher, pull
  scheduler, startup check, preflight refusals, retries, two-way sync,
  trash), one module per concern; the package docstring maps them, and
  `engine.py` documents the pause/resume state machine.
- `backend/services/rclone/`: every rclone invocation, output parsing and
  error classification; `backend/services/backup_service/`: backups,
  verification, retention, restores and snapshot browsing. Both packages
  map their modules in the package docstring.
- `frontend/src`: App Router pages in `app/`, TanStack Query hooks in
  `hooks/`, the API client in `lib/api.ts`, the `/api` proxy that adds the
  token in `proxy.ts`, translations in `i18n/locales`.
- `tui/internal`: `api` (mirrors the backend schemas, checked by contract
  tests against generated fixtures), `cli` (scripting subcommands), `ui`
  (Bubble Tea views).

## Backend

Python 3.12 in a virtual environment. `backend/requirements.lock` holds the
exact runtime pins the image ships; `requirements-dev.txt` adds the test
and lint tools on top of it.

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt
```

Run the checks the way CI does (`.github/workflows/ci.yml`), from the
repository root:

```bash
ruff check backend          # rules and their reasons: backend/ruff.toml
pyright                     # scope and mode: pyrightconfig.json

cd backend
PYTHONPATH=.. OMNISYNC_DB_PATH=/tmp/o.db OMNISYNC_LOG_PATH=/tmp/o.log \
  OMNISYNC_RCLONE_CONFIG=/tmp/rclone.conf \
  python -m pytest -q -p no:cacheprovider -n 4 --cov=backend --cov-report=term
```

- `-n 4` runs the suite in four processes (pytest-xdist); every test uses its
  own `tmp_path` and in-memory database, so they share nothing. Drop it to
  debug a single test.
- The suite runs warning-free: a new warning fails it (`backend/pytest.ini`).
- The data-safety tests run the real `rclone` binary and are skipped without
  one; CI sets `OMNISYNC_REQUIRE_RCLONE=1` so they cannot be skipped there.
  Install the version the Dockerfile pins (`RCLONE_VERSION`).
- Coverage must stay above `backend/.coveragerc`'s `fail_under`, and the
  modules that can lose data (`services/rclone/`,
  `services/sync_engine/`, `services/sync_engine_manager.py`,
  `services/profile_service.py`, `api/routes/profiles.py`) at 70% each.

Run the API directly with
`uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers`
(the flag keeps uvicorn from believing `X-Forwarded-For`, see
`backend/security.py`) after pointing the
`OMNISYNC_*_PATH` variables (and `OMNISYNC_RCLONE_CONFIG`) at writable paths,
or use `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`
for live reload in the container. `OMNISYNC_API_DOCS=1` serves `/docs`.

**Schema changes** need an Alembic revision in `backend/migrations/versions`;
the app applies pending revisions on start, after copying the database.

**Models the TUI decodes**: its contract tests read fixtures generated from
the backend's Pydantic models. After changing one, regenerate them and
commit the result (CI fails otherwise):

```bash
PYTHONPATH=. python tui/test/fixtures/gen_fixtures.py
```

**Routes and models the web UI uses**: `frontend/openapi.json` is the
backend's OpenAPI schema and `frontend/src/types/api.gen.ts` the types
generated from it. After changing a route or a request/response model,
regenerate both and commit them (CI fails otherwise); `pnpm typecheck` then
checks the hand-written types in `src/types/index.ts` against them
(`src/__tests__/api-types.contract.test.ts`):

```bash
PYTHONPATH=. python scripts/gen-openapi.py
(cd frontend && pnpm gen:types)
```

**Error answers** all have the shape `{"detail", "code", "details"}`: raise
`api_error(status, "code", "message", **details)` from
`backend/api/errors.py` and add a new code to
[docs/api-errors.md](docs/api-errors.md) (a test checks it).

## Web UI

```bash
cd frontend
pnpm install
OMNISYNC_API_TOKEN=<token> pnpm dev     # http://127.0.0.1:3000, backend at BACKEND_URL
```

The checks CI runs:

```bash
pnpm lint
pnpm typecheck
pnpm test          # Vitest; CI runs pnpm test:coverage (with thresholds)
pnpm build
pnpm exec playwright install chromium && pnpm test:e2e   # browser smoke tests against the build
```

Every user-visible string goes through the i18n files
(`src/i18n/locales/{en,de,fa}.json`); tests fail when a key is missing in a
language, a German or Persian string is left in English, or placeholders
differ. See [`frontend/README.md`](frontend/README.md) for how the UI talks
to the backend.

## Terminal UI

```bash
cd tui
gofmt -l .                      # must print nothing
go mod tidy && git diff --exit-code -- go.mod go.sum
go vet ./...
go test -race ./...             # make test
golangci-lint run               # make lint (CI uses v2.5.0)
make install                    # ~/.local/bin/osync-tui and the alias osync
```

## Workflows and images

Workflow changes must pass actionlint, as CI runs it:

```bash
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
```

Every `uses:` is pinned to a commit SHA with its release in a comment
(`@<sha>  # v7.0.1`) and must run on Node 24 (`ci.yml` shows how to check).
Base images in the Dockerfiles are pinned by tag and digest. Dependabot
updates both kinds of pin.

To check the images: `docker compose build`, then `docker compose config`
for each combination of compose files you changed.

The installer is tested end to end against images built from your checkout
(CI's `installer` job does the same):

```bash
docker build -t ghcr.io/pan-fire/omnisync-backend:9999.0.0-ci .
docker build -t ghcr.io/pan-fire/omnisync-web:9999.0.0-ci frontend
scripts/test-installer-e2e.sh 9999.0.0-ci
```

It sets `OMNISYNC_INSTALL_TEST_RELEASE_DIR`, the installer's test-only
switch: a folder with `VERSION`, `compose.yml` and `SHA256SUMS` stands in
for the GitHub release, and the images are not pulled. It is for CI and
this script only, never for an install.

## Pull requests

- **Changelog.** Add each user-visible change under `## [Unreleased]` in
  [`CHANGELOG.md`](CHANGELOG.md), in the pull request that makes it, under
  the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) headings
  (Added, Changed, Fixed, Removed, Security). Write for users: what changed
  for them, not which function moved. Internal refactors and test-only
  changes need no entry.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/):
  `type(scope): summary` in the imperative, e.g.
  `fix(backend): move blocking file writes off the event loop`. Types in use:
  `feat`, `fix`, `docs`, `test`, `refactor`, `build`, `ci`, `chore`; scopes
  are the area (`backend`, `web`, `tui`, `i18n`, `notifications`, `docker`,
  `release`, ...). The body says **why** the change is needed, not only what
  it does.
- Keep a pull request to one concern; CI must be green before merging.

## CI

Every workflow runs on GitHub-hosted runners (`ubuntu-24.04`, and
`ubuntu-24.04-arm` for the arm64 release images), so CI needs nothing but
the repository. [`ci.yml`](.github/workflows/ci.yml) is the gate for every
push and pull request: backend, web UI, TUI, actionlint, and both image
builds with a Trivy scan. Vulnerability scans (pip-audit, pnpm audit,
govulncheck, Trivy) run weekly in
[`security.yml`](.github/workflows/security.yml); CodeQL
([`codeql.yml`](.github/workflows/codeql.yml)) on every push to `main`, on
pull requests that change code, and weekly; gitleaks
([`secrets.yml`](.github/workflows/secrets.yml)) over the whole history on
every push and pull request.

Pull requests from forks run CI like any other: each job gets a fresh VM, a
read-only token and no secrets. Keep it that way:
`backend/tests/test_workflow_guards.py` fails if a job names a runner other
than the GitHub-hosted ones, if a workflow uses `pull_request_target` (it
runs with secrets and a write token), or if a workflow lacks
least-privilege `permissions:`.

### Secrets

Never commit credentials. Install the hooks once per clone:

```bash
pip install pre-commit && pre-commit install
```

They run gitleaks ([`.gitleaks.toml`](.gitleaks.toml)), detect private keys
and refuse files over 1 MB. CI runs gitleaks over the whole history, and
`backend/tests/test_repo_secrets.py` scans the tracked files with
OmniSync's own rules. Test fixtures use fake values; a value that only
looks like a secret (a public client id, a documented example key) gets an
allow-list entry with its reason in `.gitleaks.toml`. If a real secret was
pushed, rotate it first: it is leaked even if the history is rewritten.

## Releases

The version lives in [`VERSION`](VERSION); the backend reports it, the web
UI's `frontend/package.json` repeats it, and the TUI gets it through
`-ldflags`. Between releases it may hold the next release's development
version (e.g. `0.13.0-dev`), which is never tagged; builds from `main` report it.
To release:

1. `scripts/release/prepare.sh 0.13.0` bumps `VERSION` and
   `frontend/package.json`, moves the Unreleased entries into a `0.13.0`
   section, and prints the git commands to commit and tag; it runs none of
   them. `scripts/release/changelog-notes.sh 0.13.0` shows the notes. The
   changelog links the new version to a comparison with the previous
   tagged release, or, for the first release tagged here, to its tag page
   (0.10.0 was released before the repository was public and has no tag).
2. Get that commit onto `main`, then tag it there (`git tag -a v0.13.0`) and
   push the tag.
3. The tag starts [`.github/workflows/release.yml`](.github/workflows/release.yml):
   it refuses a tag that does not match `VERSION`, `frontend/package.json`
   and a changelog section, or is not on `main`; runs the whole CI suite;
   builds both images for `linux/amd64` and `linux/arm64`, each natively
   on a runner of that architecture, with an SBOM and provenance, merges
   each image's platforms into one multi-platform image, tags it in GHCR
   and signs it with cosign; builds the six `osync` binaries with a signed
   `SHA256SUMS`; and publishes the GitHub release with the changelog
   section as its notes. The repository variable `RELEASE_IMAGE_PLATFORMS`
   (e.g. `linux/amd64`) narrows the platforms. A failed run can be re-run:
   images are re-pushed and the release's files replaced.

The first push creates the GHCR packages as private; to let others pull
without logging in, make both packages public in their package settings. The README's
[Verify a release](README.md#verify-a-release) shows how users check the
signatures.

## License

By contributing you agree that your contributions are licensed under the
[GNU General Public License v3.0](LICENSE), like the rest of OmniSync.
