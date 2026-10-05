This repository is a small sample service used to exercise rentcheck's scanner.
Read the sections below before making changes.

## Build and test

Install dependencies with `npm install`, then run `npm run build` to compile
the project; the build entry point is `build.js`. Run the unit suite with
`npm test` before every commit.

## Testing conventions

Tests live under `tests/` and mirror the layout of `src/`. The helper utilities
are in `src/utils.js`. Prefer small, focused tests over broad integration ones.

## Deployment

Deploys are driven by `make deploy`, which runs `deploy.js` and uploads the
result. The deploy script reads its config from `deploy.config.json`. Never
deploy from a dirty working tree.

## Known stale reference

Historical notes referenced `scripts/legacy_migrate.sh`, which no longer exists
in this repository. This section exists to prove the scanner flags it.
