# OmniSync web UI: a production Next.js server (output: 'standalone').
#
#   docker build -t omnisync-frontend ./frontend
#
# Runtime settings (read per request by src/proxy.ts, not at build time):
#   BACKEND_URL          where /api requests go (default http://127.0.0.1:8000)
#   OMNISYNC_API_TOKEN   the backend's API token, added to every /api request
#   OMNISYNC_UI_PASSWORD_HASH and friends: the optional login (see the README)
#
# Built for linux/amd64 and linux/arm64. Every stage runs on the target
# platform (under QEMU when cross-building), because the standalone output
# copies the native packages it traces (e.g. sharp) for the platform that
# installed them. Base images are pinned by tag and digest (the digest is what
# is pulled); Dependabot (docker ecosystem) moves both together.

# ---- Dependencies ----
FROM node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS deps

# Exact pnpm release, same major as CI (.github/workflows/ci.yml).
ARG PNPM_VERSION=10.34.5
RUN npm install -g "pnpm@${PNPM_VERSION}" && npm cache clean --force

WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# ---- Build ----
FROM deps AS build

ENV NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN pnpm build

# ---- Runtime ----
FROM node:22-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS runtime

LABEL org.opencontainers.image.source="https://github.com/pan-fire/OmniSync" \
      org.opencontainers.image.licenses="GPL-3.0-only" \
      org.opencontainers.image.title="OmniSync web UI"

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    TZ=UTC

# The server needs only node: drop the bundled npm and corepack, which
# carry their own (unused) dependencies and advisories.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
    /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack

WORKDIR /app

# The standalone server with only the dependencies it traces, plus the
# static assets it does not copy itself. Owned by root and read-only for
# the unprivileged `node` user the server runs as.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
# `node scripts/hash-password.mjs` makes OMNISYNC_UI_PASSWORD_HASH (no dependencies).
COPY --from=build /app/scripts/hash-password.mjs ./scripts/hash-password.mjs

# `node` (1000:1000 in the base image), numeric so a runtime can verify that
# it is not root (e.g. Kubernetes runAsNonRoot).
USER 1000:1000

EXPOSE 3000

# The same check as docker-compose.yml: /healthz answers without a login and
# without calling the backend, so this checks the Next.js server only. Uses
# $PORT, as server.js does.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/healthz').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]

CMD ["node", "server.js"]
