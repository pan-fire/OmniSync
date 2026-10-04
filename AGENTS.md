# AGENTS.md

Guidance for coding agents working on OmniSync. People: the same rules,
with more background, are in [CONTRIBUTING.md](CONTRIBUTING.md).

OmniSync moves and deletes people's files. A change that touches syncing,
backups or restores needs a test that proves it cannot lose data, run
against the real `rclone` binary where it matters.

## Layout

```text
backend/    FastAPI app, Python 3.12, async SQLAlchemy + Alembic
  api/        routes/, schemas.py (Pydantic models), errors.py (error envelope)
  services/   sync_engine/, rclone/, backup_service/, notifications, wizard
  migrations/ Alembic revisions, applied on start
  tests/      pytest (warnings are errors)
frontend/   Next.js web UI (Node 24 LTS, pnpm 10), Vitest, Playwright smoke tests
  src/i18n/locales/{en,de,fa}.json   every user-visible string
  src/types/api.gen.ts               generated from frontend/openapi.json
tui/        `osync`, Go 1.27 Bubble Tea TUI and Cobra CLI
  test/fixtures/                     contract fixtures generated from the backend models
docs/       user guide (docs/gem), API error codes (docs/api-errors.md)
scripts/    gen-openapi.py, docker-entrypoint.sh, release/
```

## Checks

Run what your change touches; CI (`.github/workflows/ci.yml`) runs all of it.

Backend, from the repository root (`pip install -r backend/requirements-dev.txt`):

```bash
ruff check backend scripts tui/test/fixtures
pyright
cd backend && PYTHONPATH=.. OMNISYNC_DB_PATH=/tmp/o.db OMNISYNC_LOG_PATH=/tmp/o.log \
  OMNISYNC_RCLONE_CONFIG=/tmp/rclone.conf python -m pytest -q -p no:cacheprovider -n auto
```

The suite runs with `filterwarnings = error`: close sockets and files, await
mocks, dispose engines. The data-safety tests skip without `rclone` on PATH
(CI sets `OMNISYNC_REQUIRE_RCLONE=1`, so they cannot skip there).

Web UI, from `frontend/`:

```bash
pnpm install --frozen-lockfile
pnpm lint && pnpm typecheck && pnpm test && pnpm build
pnpm test:e2e     # after pnpm build; needs `pnpm exec playwright install chromium`
```

TUI, from `tui/`:

```bash
gofmt -l .        # must print nothing
go mod tidy && git diff --exit-code -- go.mod go.sum
go vet ./... && go test -race ./...
golangci-lint run ./...
```

Workflows: `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12`.

## Generated files

Regenerate and commit these in the same change; CI fails on drift.

- A route or request/response model changed:
  `PYTHONPATH=. python scripts/gen-openapi.py`, then
  `cd frontend && pnpm gen:types`.
- A model the TUI decodes changed:
  `PYTHONPATH=. python tui/test/fixtures/gen_fixtures.py`.
- A database schema change needs a new revision in
  `backend/migrations/versions`.

## Conventions

- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/),
  `type(scope): summary` in the imperative (`feat`, `fix`, `docs`, `test`,
  `refactor`, `build`, `ci`, `chore`; scopes such as `backend`, `web`,
  `tui`, `i18n`, `docker`, `release`). The body says why.
- **Changelog:** user-visible changes go under `## [Unreleased]` in
  `CHANGELOG.md` (Keep a Changelog headings), written for users.
- **Strings:** every user-visible web UI string needs `en`, `de` and `fa`.
  Tests fail on a missing key, an untranslated German or Persian string, or
  differing placeholders. In Persian, put an LRM (U+200E) before path and
  other left-to-right tokens.
- **CSS:** logical utilities only (`ms-`, `pe-`, `start-`, `text-start`),
  never `ml-`/`pr-`/`left-`/`text-left`; `src/__tests__/rtl.test.tsx`
  enforces it.
- **Errors:** every error answer is `{"detail", "code", "details"}`. Raise
  `api_error(status, "code", "message", **details)` from
  `backend/api/errors.py`, and list a new code in `docs/api-errors.md` (a
  test checks it).
- **No rclone output to clients:** rclone stderr, exception text, paths and
  tokens go to the server log (through `redact_secrets` in
  `backend/services/rclone/errors.py`), never into an API answer.
- **Logging:** use a module logger (`logger = logging.getLogger(__name__)`
  under `backend.`), never `print`. Never log a secret: the handlers mask
  known shapes and registered values (`backend/logging_setup.py`,
  `register_secret` for a new kind of secret), but that is the safety net,
  not the rule. Record user actions with `@audited(...)` or `audit(...)`
  from `backend/audit.py` (logger `backend.audit`), with names and ids
  only, never request bodies or settings values. The web UI server logs
  through `logServerEvent` (`frontend/src/lib/server-log.ts`), with a new
  event type added to `ServerEvent`. Operators' view: the "Logs" section of
  `docs/gem/operations.md`.
- **Secrets:** never commit credentials; test fixtures use fake values.
  `pre-commit install` runs gitleaks locally.
- **Workflows:** pin every `uses:` to a commit SHA with its version in a
  comment, run jobs on GitHub-hosted runners only, and never use
  `pull_request_target` (`backend/tests/test_workflow_guards.py`).
