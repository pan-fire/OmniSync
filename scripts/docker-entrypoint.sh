#!/bin/sh
set -e

# Map the container process to the host user's UID/GID so files created by
# rclone (pulls) are owned by the real user, not root.
PUID=${PUID:-1000}
PGID=${PGID:-1000}
DATA_DIR=/data/omnisync

log () { echo "entrypoint: $*" >&2; }

# Started as a non-root user already (`user:` in compose, `docker run -u`):
# nothing to map or fix, and no way to; run as that user.
if [ "$(id -u)" != 0 ]; then
    exec "$@"
fi

case "$PUID:$PGID" in
    *[!0-9:]*|:*|*:)
        log "PUID and PGID must be numeric ids (got PUID=$PUID PGID=$PGID)"
        exit 64
        ;;
esac

if [ "$PUID" = 0 ]; then
    log "WARNING: PUID=0, so OmniSync runs as root inside the container."
    log "WARNING: rclone then writes your files as root, and a bug or a"
    log "WARNING: malicious remote has root's reach over the mounted folders."
    log "WARNING: Set PUID/PGID to your own ids (id -u, id -g) in .env."
fi

# Create group and user matching the host UID/GID
if ! getent group "$PGID" > /dev/null 2>&1; then
    groupadd -g "$PGID" omnisync
fi
if ! getent passwd "$PUID" > /dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -d /tmp -s /sbin/nologin omnisync
fi

# Make the data directory writable by the mapped user. Only entries owned by
# someone else are changed (after a PUID change, a restore from a backup, or
# a `docker exec` as root), so a normal start rewrites nothing; a blanket
# `chown -R` would touch every file on every start. -h changes a symlink
# itself, never what it points to.
mkdir -p "$DATA_DIR"
changed=$(find "$DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) \
    -exec chown -h "$PUID:$PGID" {} + -print | wc -l)
if [ "$changed" -gt 0 ]; then
    log "gave $changed entries in $DATA_DIR to $PUID:$PGID"
fi

exec gosu "$PUID:$PGID" "$@"
