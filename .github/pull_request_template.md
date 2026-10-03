## Summary

<!-- What does this change and why? Link the issue it fixes (Fixes #123). -->

## Testing

<!-- How you checked it: the tests you added or ran, and anything you tried by hand. -->

- [ ] Backend: `ruff check backend`, `pyright`, `pytest -n 4` (see CONTRIBUTING.md)
- [ ] Web UI: `pnpm lint && pnpm typecheck && pnpm test && pnpm build`
- [ ] TUI: `gofmt`, `go vet`, `go test -race ./...`, `golangci-lint run`
- [ ] Not applicable (docs only)

## Checklist

- [ ] User-visible changes are described under `## [Unreleased]` in `CHANGELOG.md` (or this change has none)
- [ ] Changes that can delete or overwrite files have a test proving they do not lose data
- [ ] New UI strings are translated in `en`, `de` and `fa`
- [ ] Model changes come with regenerated TUI fixtures (`tui/test/fixtures/gen_fixtures.py`) and, for schema changes, an Alembic migration
- [ ] No tokens, passwords or personal paths in code, logs or screenshots
