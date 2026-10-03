# OmniSync backend: FastAPI, rclone and the entrypoint that drops to PUID:PGID.
#
#   docker build -t omnisync-backend .
#
# Built for linux/amd64 and linux/arm64 (the release images cover both).
# Base images are pinned by tag and digest (the digest is what is pulled; the
# tag says what it is); Dependabot (docker ecosystem) moves both together.

# ---- Builder stage ----
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS builder

WORKDIR /build

# Exact pins (see backend/requirements.lock) so a rebuild never picks up
# an untested dependency release.
COPY backend/requirements.lock .
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock

# ---- Runtime stage ----
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

# Pinned rclone release, verified against the published SHA256SUMS.
ARG RCLONE_VERSION=1.75.1
ARG RCLONE_SHA256_AMD64=982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab
ARG RCLONE_SHA256_ARM64=03f2504174034b6d004152ed7369251c9a9ec1f7e0836eda420f5c7a5ec0dff9
# Set by BuildKit for each target platform; the default serves the legacy
# builder. Only amd64 and arm64 are supported: rclone ships linux/arm-v7 too,
# but several Python dependencies (uvloop, httptools) have no armv7 wheels and
# would need a compiler toolchain in the builder stage.
ARG TARGETARCH=amd64

# rclone (verified download), gosu (the entrypoint drops root with it), and
# dbus + libnotify-bin: `notify-send` for native desktop notifications through
# the host's session bus (opt-in, docker-compose.dbus.yml). The two packages
# add a few MB; without the override they are unused but harmless.
# Debian package versions are not pinned (DL3008): the base image digest fixes
# the Debian release, and pinned versions vanish from the mirror on updates.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip gosu dbus libnotify-bin \
    && case "$TARGETARCH" in \
         amd64) sum="$RCLONE_SHA256_AMD64" ;; \
         arm64) sum="$RCLONE_SHA256_ARM64" ;; \
         *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && zip="rclone-v${RCLONE_VERSION}-linux-${TARGETARCH}.zip" \
    && curl -fsSLo "/tmp/$zip" "https://downloads.rclone.org/v${RCLONE_VERSION}/$zip" \
    && echo "$sum  /tmp/$zip" | sha256sum -c - \
    && unzip -q "/tmp/$zip" -d /tmp \
    && install -m 0755 "/tmp/rclone-v${RCLONE_VERSION}-linux-${TARGETARCH}/rclone" /usr/local/bin/rclone \
    && rm -rf /tmp/rclone-* \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Create data and sync directories
RUN mkdir -p /data/omnisync /sync

WORKDIR /app

COPY backend/ ./backend/
# The release version the backend reports (backend/version.py).
COPY VERSION ./VERSION
COPY scripts/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 8000 serves the API and the OAuth callback; nothing else listens.
EXPOSE 8000

# Times in logs and schedules follow TZ (tzdata is in the base image); set
# TZ=Europe/Berlin etc. at run time.
ENV TZ=UTC

# The same check as docker-compose.yml, so `docker run` gets it too. /health
# needs no token and answers 503 when the database or rclone is unavailable,
# which makes urlopen raise and the check fail.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]

# --no-proxy-headers: the client address is the TCP peer. uvicorn would
# otherwise believe X-Forwarded-For from loopback, letting a client there
# pick any address to dodge (or aim) the failed-token throttle
# (backend/security.py). The OAuth redirect reads the web UI's
# X-Forwarded-Host/-Proto/-Prefix itself (api/routes/wizard.py), so nothing
# needs uvicorn's rewriting. UVICORN_PROXY_HEADERS covers a `command:`
# override that leaves the flag out.
ENV UVICORN_PROXY_HEADERS=false
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
