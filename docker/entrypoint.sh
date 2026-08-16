#!/bin/sh
# Tome container entrypoint.
#
# Maps the internal "tome" user to the host UID/GID given by PUID/PGID
# (LinuxServer.io convention) so bind-mounted /data, /books and /bindery are
# accessed as the same user that owns them on the host. Defaults to 1000:1000,
# which is exactly what the image did before this script existed, so existing
# installs are unaffected.
#
# If the container is started with an explicit `user:` (or `--user`) it is not
# root here and cannot remap anything; in that case it simply execs the app as
# whatever user it was given, which is the pre-existing behaviour.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    case "$PUID$PGID" in
        *[!0-9]*) echo "tome: PUID/PGID must be numeric (got PUID=$PUID PGID=$PGID)" >&2; exit 1 ;;
    esac

    if [ "$(id -g tome)" != "$PGID" ]; then
        groupmod -o -g "$PGID" tome
    fi
    if [ "$(id -u tome)" != "$PUID" ]; then
        usermod -o -u "$PUID" tome
    fi

    # Tome's own state must be writable by the runtime user. Only touch files
    # that are not already owned correctly, so a large cover cache on a default
    # install costs one directory walk and zero chown calls. The library and
    # bindery are the user's data (and may be mounted read-only): never chown.
    for d in "${TOME_DATA_DIR:-/data}" /home/tome; do
        [ -d "$d" ] || continue
        find "$d" \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown -h "$PUID:$PGID" {} + 2>/dev/null || true
    done

    echo "tome: running as uid=$PUID gid=$PGID"
    exec gosu tome "$@"
fi

exec "$@"
