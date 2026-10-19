#!/usr/bin/env bash
# Empties the CI runner's own Docker daemon — the runner's hook at the start and at the end of every job (ICCINT-261).
#
# On 15.10.2026 GitHub cancelled a NEX Manager CI job mid-test and its database kept running for four days on the
# server's live Docker. A runner now works against a daemon of its own (docker-compose.yml next to this file) and that
# daemon is emptied completely — nothing else lives in it. The start of a job also cleans up after a job whose end
# never ran (a crashed runner, a server restart). Tagged images stay: the next build reuses them.
#
# SAFEGUARD: empties only the daemon named CI_DOCKER_NAME. The server's live Docker has another name — were the runner
# ever handed its socket, the script stops without touching anything and the job fails.
set -uo pipefail

expected="${CI_DOCKER_NAME:-}"
if [[ -z "$expected" ]]; then
  echo "cleanup.sh: CI_DOCKER_NAME is missing — emptying nothing" >&2
  exit 1
fi
if ! name=$(docker info --format '{{.Name}}' 2>/dev/null); then
  echo "cleanup.sh: the runner's own Docker does not answer (${DOCKER_HOST:-DOCKER_HOST is missing})" >&2
  exit 1
fi
if [[ "$name" != "$expected" ]]; then
  echo "cleanup.sh: this Docker is called '$name', not '$expected' — not the runner's own, emptying nothing" >&2
  exit 1
fi

mapfile -t containers < <(docker ps -aq)
if ((${#containers[@]})); then
  docker rm -f "${containers[@]}" >/dev/null || exit 1
fi
docker volume prune -af >/dev/null || exit 1
docker network prune -f >/dev/null || exit 1
docker image prune -f >/dev/null || exit 1
echo "cleanup.sh: own Docker ${name} emptied (containers: ${#containers[@]})"
