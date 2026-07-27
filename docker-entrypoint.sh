#!/usr/bin/env bash
# Bring the database schema to head, THEN hand over to the container command.
#
# Why this exists (CR-V2-063 post-mortem): no deploy path applied migrations. The documented recipe
# is "build images → bump the tags in the compose → docker compose up -d", and nothing in it runs
# `alembic upgrade head`. Shipping a schema change therefore depended on a human remembering an
# undocumented extra step — and forgetting it does not fail loudly, it fails when a user opens the
# affected screen and gets a 500 from a missing table. Baking it into the image is the only variant
# that cannot be bypassed: wherever this image runs — dev, UAT, another host — the schema matches
# the code it ships with.
#
# Fails LOUDLY: a migration error exits non-zero, so the container never serves a request against a
# schema it does not match. That is the correct trade — a container that refuses to start is visible
# in `docker ps`; a running container serving 500s from one screen is not.
set -euo pipefail

if [ "${SKIP_MIGRATIONS:-0}" = "1" ]; then
    echo "entrypoint: SKIP_MIGRATIONS=1 — schema left untouched, starting anyway"
else
    # alembic's env.py imports `backend.config.settings`, so /app must be importable. It is the
    # WORKDIR, but PYTHONPATH is not set by default and `alembic` is not run through `python -m`.
    export PYTHONPATH="${PYTHONPATH:-/app}"

    # `depends_on: db: service_healthy` covers the ordinary case, but a compose file without that
    # condition (or a DB that accepts connections a moment before it is ready) would otherwise
    # crash-loop the backend. Retry briefly, then give up loudly.
    attempts=10
    for attempt in $(seq 1 "$attempts"); do
        if alembic upgrade head; then
            echo "entrypoint: database schema is at head"
            break
        fi
        if [ "$attempt" -eq "$attempts" ]; then
            echo "entrypoint: migrations failed after ${attempts} attempts — refusing to start" >&2
            exit 1
        fi
        echo "entrypoint: migration attempt ${attempt}/${attempts} failed, retrying in 3s…" >&2
        sleep 3
    done
fi

exec "$@"
